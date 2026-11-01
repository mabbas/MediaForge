/**
 * Automated Electron Startup and Runtime Security Test
 * Launches Electron with real BrowserWindow, preload, and renderer bundle.
 */

const { app, BrowserWindow, ipcMain, protocol } = require('electron');
const path = require('path');
const fs = require('fs');

// Register custom protocol scheme
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'mediaforge-media',
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
      corsEnabled: true,
    },
  },
]);

// Mock IPC handlers as in main.ts
ipcMain.handle('app:version', () => '0.1.0');
ipcMain.handle('app:platform', () => process.platform);
ipcMain.handle('auth:get-session', () => ({ accessToken: null, user: null }));

let exitCode = 0;
const consoleMessages = [];
const consoleErrors = [];

app.whenReady().then(async () => {
  console.log('[ELECTRON-TEST] Electron app ready on Windows 11');
  console.log('[ELECTRON-TEST] Platform:', process.platform, 'Arch:', process.arch);
  console.log('[ELECTRON-TEST] Electron version:', process.versions.electron);
  console.log('[ELECTRON-TEST] Chrome version:', process.versions.chrome);
  console.log('[ELECTRON-TEST] Node version:', process.versions.node);

  const preloadPath = path.resolve(__dirname, '../dist/electron/preload.js');
  const rendererPath = path.resolve(__dirname, '../dist/renderer/index.html');

  if (!fs.existsSync(preloadPath)) {
    console.error('[ELECTRON-TEST] Preload script not found at', preloadPath);
    app.exit(1);
    return;
  }
  if (!fs.existsSync(rendererPath)) {
    console.error('[ELECTRON-TEST] Renderer HTML not found at', rendererPath);
    app.exit(1);
    return;
  }

  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    show: false, // Offscreen/headless mode for CI/automation
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  win.webContents.on('console-message', (event) => {
    const msg = `[RENDERER-CONSOLE ${event.level}] ${event.message}`;
    consoleMessages.push(msg);
    if (event.level >= 3) {
      // Error level
      consoleErrors.push(event.message);
    }
  });

  win.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error(`[ELECTRON-TEST] did-fail-load: ${errorCode} - ${errorDescription}`);
    exitCode = 1;
  });

  try {
    console.log('[ELECTRON-TEST] Loading renderer file:', rendererPath);
    await win.loadFile(rendererPath);
    console.log('[ELECTRON-TEST] Renderer file loaded successfully');

    // Wait 500ms for React root mounting
    await new Promise((resolve) => setTimeout(resolve, 500));

    // Test Invariants inside the renderer world
    const checks = await win.webContents.executeJavaScript(`
      (() => {
        return {
          hasRoot: !!document.getElementById('root'),
          rootHtmlLength: document.getElementById('root') ? document.getElementById('root').innerHTML.length : 0,
          rootText: document.getElementById('root') ? document.getElementById('root').innerText.substring(0, 100) : '',
          hasMediaForge: typeof window.mediaforge === 'object' && window.mediaforge !== null,
          hasRequire: typeof window.require !== 'undefined',
          hasProcess: typeof window.process !== 'undefined',
          hasNodeBuffer: typeof window.Buffer !== 'undefined',
          hasIpcRenderer: typeof window.ipcRenderer !== 'undefined',
        };
      })()
    `);

    console.log('[ELECTRON-TEST] Runtime Invariant Checks:', JSON.stringify(checks, null, 2));

    if (!checks.hasRoot || checks.rootHtmlLength === 0) {
      console.error('[ELECTRON-TEST] React did NOT mount into #root!');
      exitCode = 1;
    } else {
      console.log('[ELECTRON-TEST] PASS: React mounted into #root with content length', checks.rootHtmlLength);
    }

    if (!checks.hasMediaForge) {
      console.error('[ELECTRON-TEST] window.mediaforge is NOT exposed via contextBridge!');
      exitCode = 1;
    } else {
      console.log('[ELECTRON-TEST] PASS: window.mediaforge exposed via contextBridge');
    }

    if (checks.hasRequire || checks.hasProcess || checks.hasNodeBuffer || checks.hasIpcRenderer) {
      console.error('[ELECTRON-TEST] Node security breach! Node globals leaked to renderer!');
      exitCode = 1;
    } else {
      console.log('[ELECTRON-TEST] PASS: Node.js globals (require, process, Buffer, ipcRenderer) are strictly blocked');
    }

    // Test calling an IPC bridge method from inside renderer
    const ipcResult = await win.webContents.executeJavaScript(`
      window.mediaforge.getAppVersion().then(v => ({ success: true, version: v })).catch(e => ({ success: false, error: e.message }))
    `);

    console.log('[ELECTRON-TEST] IPC call result:', JSON.stringify(ipcResult));
    if (!ipcResult.success || ipcResult.version !== '0.1.0') {
      console.error('[ELECTRON-TEST] IPC bridge call failed!');
      exitCode = 1;
    } else {
      console.log('[ELECTRON-TEST] PASS: IPC bridge call window.mediaforge.getAppVersion() succeeded');
    }

    // Check for renderer errors
    if (consoleErrors.length > 0) {
      console.error('[ELECTRON-TEST] Renderer logged console errors:', consoleErrors);
      exitCode = 1;
    } else {
      console.log('[ELECTRON-TEST] PASS: 0 renderer console errors logged');
    }
  } catch (err) {
    console.error('[ELECTRON-TEST] Uncaught test exception:', err);
    exitCode = 1;
  } finally {
    win.destroy();
    console.log('[ELECTRON-TEST] Exiting with code:', exitCode);
    app.exit(exitCode);
  }
});
