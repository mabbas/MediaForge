import { app, BrowserWindow, ipcMain, protocol, shell, safeStorage } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import * as crypto from 'crypto';
import * as http from 'http';
import * as https from 'https';

// Register custom protocol scheme as privileged before app is ready
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

let mainWindow: BrowserWindow | null = null;
const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;

// In-memory secure session store (Refresh token NEVER shared with renderer)
interface SecureSession {
  accessToken: string;
  refreshToken: string | null;
  user: {
    user_id: string;
    tenant_id: string;
    email?: string;
    display_name?: string;
    role: string;
    tier: string;
  };
}

let activeSession: SecureSession | null = null;
let refreshMutexPromise: Promise<{ accessToken: string; expiresIn: number } | null> | null = null;
let customBackendUrl: string | null = null;

// Backend API Base URL with strict Loopback HTTP and mandatory Remote HTTPS validation
function getBackendBaseUrl(): string {
  const envUrl = customBackendUrl || process.env.MEDIAFORGE_API_URL || 'http://127.0.0.1:58000/api/v1';
  try {
    const trimmed = envUrl.replace(/\/+$/, '');
    const parsed = new URL(trimmed);
    const isLoopback =
      parsed.hostname === 'localhost' ||
      parsed.hostname === '127.0.0.1' ||
      parsed.hostname === '::1';

    if (parsed.protocol === 'http:' && !isLoopback) {
      console.warn('[SECURITY] Insecure HTTP backend prohibited for remote endpoints; falling back to default loopback API.');
      return 'http://127.0.0.1:58000/api/v1';
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      console.warn('[SECURITY] Unsupported protocol in backend URL; falling back to default loopback API.');
      return 'http://127.0.0.1:58000/api/v1';
    }
    return trimmed;
  } catch {
    return 'http://127.0.0.1:58000/api/v1';
  }
}

// Multi-tenant & Per-Account Media Cache Structure:
// %APPDATA%/MediaForge/media_cache/<tenant_id>/<user_id>/<artifact_id>/<filename>
function getCacheRoot(): string {
  const root = path.join(app.getPath('userData'), 'media_cache');
  if (!fs.existsSync(root)) {
    fs.mkdirSync(root, { recursive: true });
  }
  return root;
}

function getTenantDir(tenantId: string): string {
  const safeTenantId = tenantId.replace(/[^a-zA-Z0-9_-]/g, '_');
  const dir = path.join(getCacheRoot(), safeTenantId);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  return dir;
}

function getUserCacheDir(tenantId: string, userId: string): string {
  const safeTenantId = tenantId.replace(/[^a-zA-Z0-9_-]/g, '_');
  const safeUserId = userId.replace(/[^a-zA-Z0-9_-]/g, '_');
  const dir = path.join(getCacheRoot(), safeTenantId, safeUserId);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  return dir;
}

function getSessionFilePath(): string {
  return path.join(app.getPath('userData'), 'session.vault');
}

// Load encrypted session on startup if safeStorage available
function loadPersistedSession(): void {
  try {
    const vaultPath = getSessionFilePath();
    if (!fs.existsSync(vaultPath)) return;
    const rawData = fs.readFileSync(vaultPath);

    if (safeStorage.isEncryptionAvailable()) {
      const sessionJson = safeStorage.decryptString(rawData);
      activeSession = JSON.parse(sessionJson);
    } else {
      // OS safeStorage unavailable: fail closed against unencrypted disk tokens
      activeSession = null;
      try {
        fs.unlinkSync(vaultPath);
      } catch {}
      console.warn('[SECURITY] OS safeStorage encryption unavailable. Operating in session-only memory mode.');
    }
  } catch (err) {
    activeSession = null;
  }
}

function persistSession(): void {
  try {
    const vaultPath = getSessionFilePath();
    if (!activeSession) {
      if (fs.existsSync(vaultPath)) fs.unlinkSync(vaultPath);
      return;
    }

    if (safeStorage.isEncryptionAvailable()) {
      const sessionJson = JSON.stringify(activeSession);
      const encrypted = safeStorage.encryptString(sessionJson);
      fs.writeFileSync(vaultPath, encrypted);
    } else {
      // Fail closed against plaintext persistence
      if (fs.existsSync(vaultPath)) {
        try {
          fs.unlinkSync(vaultPath);
        } catch {}
      }
      console.warn('[SECURITY] OS safeStorage encryption unavailable. In-memory session active (no disk persistence).');
    }
  } catch (err) {
    console.error('Failed to persist session safely:', err);
  }
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: '#0f172a',
    title: 'MediaForge Desktop',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
    },
  });

  // Strict Navigation Control
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const isAllowed = isDev
      ? url.startsWith('http://localhost:5173') || url.startsWith('http://127.0.0.1:5173')
      : url.startsWith('file://');
    if (!isAllowed) {
      event.preventDefault();
    }
  });

  // Block Developer Tools shortcuts and opening in production
  if (!isDev) {
    mainWindow.webContents.on('before-input-event', (event, input) => {
      if (
        input.key === 'F12' ||
        (input.control && input.shift && ['i', 'r', 'j'].includes(input.key.toLowerCase()))
      ) {
        event.preventDefault();
      }
    });

    mainWindow.webContents.on('devtools-opened', () => {
      console.warn('[SECURITY] DevTools opening prohibited in production build; closing immediately.');
      mainWindow?.webContents.closeDevTools();
    });
  }

  // Disallow popups and validate external links
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
        shell.openExternal(url);
      }
    } catch {
      // Ignore invalid URLs
    }
    return { action: 'deny' };
  });

  // Deny arbitrary web permission requests
  mainWindow.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });

  const devServerUrl = process.env.ELECTRON_DEV_URL || 'http://localhost:5173';

  // Apply CSP headers dynamically with backend origin binding
  mainWindow.webContents.session.webRequest.onHeadersReceived((details, callback) => {
    let backendOrigin = 'http://127.0.0.1:58000';
    try {
      backendOrigin = new URL(getBackendBaseUrl()).origin;
    } catch {}

    const csp = isDev
      ? `default-src 'self' http://localhost:5173; script-src 'self' 'unsafe-inline' 'unsafe-eval' http://localhost:5173; style-src 'self' 'unsafe-inline' http://localhost:5173; connect-src 'self' ${backendOrigin} http://localhost:5173 ws://localhost:5173 mediaforge-media:; media-src 'self' mediaforge-media: blob:; img-src 'self' data: mediaforge-media: http://localhost:5173; font-src 'self' data:;`
      : `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ${backendOrigin} mediaforge-media:; media-src 'self' mediaforge-media: blob:; img-src 'self' data: mediaforge-media:; font-src 'self' data:;`;

    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [csp],
      },
    });
  });

  if (process.env.NODE_ENV === 'production' || process.env.SERVE_LOCAL_BUILD === '1' || app.isPackaged) {
    mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'));
  } else {
    fetch(devServerUrl, { method: 'HEAD' })
      .then(() => {
        mainWindow?.loadURL(devServerUrl);
      })
      .catch(() => {
        mainWindow?.loadFile(path.join(__dirname, '../renderer/index.html'));
      });
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// Register Custom Streaming Protocol mediaforge-media:// with HTTP 206 Range & Account Isolation
function setupCustomProtocol(): void {
  protocol.handle('mediaforge-media', async (request) => {
    try {
      // 1. Authentication check: requires active session
      if (!activeSession || !activeSession.user || !activeSession.user.tenant_id || !activeSession.user.user_id) {
        return new Response('Unauthorized: active authenticated session required for playback', {
          status: 401,
        });
      }

      const activeTenantId = activeSession.user.tenant_id;
      const activeUserId = activeSession.user.user_id;
      const role = activeSession.user.role;

      const url = new URL(request.url);
      // Expected URL pattern: mediaforge-media://playback/<artifactId>
      const artifactId = url.pathname.replace(/^\/+/, '').replace(/[^a-zA-Z0-9_-]/g, '');
      if (!artifactId) {
        return new Response('Invalid media playback identifier', { status: 400 });
      }

      const userDir = getUserCacheDir(activeTenantId, activeUserId);
      let targetDir = path.join(userDir, artifactId);

      // Check user-level cache first
      let resolvedDir = path.resolve(targetDir);
      let found = resolvedDir.startsWith(path.resolve(userDir)) && fs.existsSync(resolvedDir);

      // If not in current user's cache, allow tenant owner/admin to locate it in tenant scope
      if (!found && (role === 'owner' || role === 'admin')) {
        const tenantDir = getTenantDir(activeTenantId);
        if (fs.existsSync(tenantDir)) {
          const userFolders = fs.readdirSync(tenantDir);
          for (const uFolder of userFolders) {
            const candidate = path.join(tenantDir, uFolder, artifactId);
            if (fs.existsSync(candidate)) {
              targetDir = candidate;
              resolvedDir = path.resolve(targetDir);
              found = true;
              break;
            }
          }
        }
      }

      // Member users attempting to access another user's artifact or non-existent artifact get 404
      if (!found) {
        return new Response('Media artifact not found in account cache', {
          status: 404,
        });
      }

      // Check directory contents
      const files = fs.readdirSync(resolvedDir).filter((f) => !f.endsWith('.part'));
      if (files.length === 0) {
        return new Response('Media file not found in cache', { status: 404 });
      }

      const filePath = path.join(resolvedDir, files[0]);

      // Disallow symlinks
      const lstat = fs.lstatSync(filePath);
      if (lstat.isSymbolicLink()) {
        return new Response('Forbidden: Symbolic links not permitted', { status: 403 });
      }

      const stat = fs.statSync(filePath);
      const fileSize = stat.size;

      // Determine MIME type
      const ext = path.extname(filePath).toLowerCase();
      let mimeType = 'video/mp4';
      if (ext === '.webm') mimeType = 'video/webm';
      else if (ext === '.mkv') mimeType = 'video/x-matroska';
      else if (ext === '.mp3') mimeType = 'audio/mpeg';

      const rangeHeader = request.headers.get('range');

      if (rangeHeader) {
        // Parse HTTP 206 Byte Range:
        // Case A: "bytes=100-200"
        // Case B: "bytes=100-" (start to end)
        // Case C: "bytes=-500" (suffix range: last 500 bytes)
        const match = rangeHeader.match(/bytes=(\d*)-(\d*)/);
        if (!match) {
          return new Response('Malformed range header', { status: 400 });
        }

        const rawStart = match[1];
        const rawEnd = match[2];

        let start: number;
        let end: number;

        if (rawStart === '' && rawEnd !== '') {
          // Suffix range: last N bytes
          const suffixLength = parseInt(rawEnd, 10);
          if (isNaN(suffixLength) || suffixLength <= 0) {
            return new Response('Invalid suffix length', { status: 416 });
          }
          start = Math.max(0, fileSize - suffixLength);
          end = fileSize - 1;
        } else if (rawStart !== '' && rawEnd === '') {
          // Open-ended range: from start to EOF
          start = parseInt(rawStart, 10);
          end = fileSize - 1;
        } else if (rawStart !== '' && rawEnd !== '') {
          // Standard range
          start = parseInt(rawStart, 10);
          end = parseInt(rawEnd, 10);
        } else {
          return new Response('Invalid range specifier', { status: 416 });
        }

        // Validate range bounds
        if (isNaN(start) || isNaN(end) || start < 0 || start >= fileSize || end < start) {
          return new Response('Requested range not satisfiable', {
            status: 416,
            headers: {
              'Content-Range': `bytes */${fileSize}`,
            },
          });
        }

        end = Math.min(end, fileSize - 1);
        const chunkLength = end - start + 1;

        const stream = fs.createReadStream(filePath, { start, end });
        const webStream = new ReadableStream({
          start(controller) {
            stream.on('data', (chunk) => controller.enqueue(chunk));
            stream.on('end', () => controller.close());
            stream.on('error', (err) => controller.error(err));
          },
          cancel() {
            stream.destroy();
          },
        });

        return new Response(webStream, {
          status: 206,
          headers: {
            'Content-Range': `bytes ${start}-${end}/${fileSize}`,
            'Accept-Ranges': 'bytes',
            'Content-Length': chunkLength.toString(),
            'Content-Type': mimeType,
          },
        });
      } else {
        // Full file
        const stream = fs.createReadStream(filePath);
        const webStream = new ReadableStream({
          start(controller) {
            stream.on('data', (chunk) => controller.enqueue(chunk));
            stream.on('end', () => controller.close());
            stream.on('error', (err) => controller.error(err));
          },
          cancel() {
            stream.destroy();
          },
        });

        return new Response(webStream, {
          status: 200,
          headers: {
            'Content-Length': fileSize.toString(),
            'Content-Type': mimeType,
            'Accept-Ranges': 'bytes',
          },
        });
      }
    } catch (err: any) {
      console.error('Custom protocol playback error:', err);
      return new Response('Internal media error: ' + (err?.message || ''), { status: 500 });
    }
  });
}

// App Lifecycle
app.whenReady().then(() => {
  // Production Debugging Lockdown:
  // Packaged releases must NEVER allow remote debugging or inspection switches.
  // Fuses enforce Node-level lockdown, while this enforces Chromium command-line lockdown.
  if (app.isPackaged) {
    const prohibitedSwitches = [
      'remote-debugging-port',
      'remote-debugging-pipe',
      'remote-allow-origins',
      'inspect',
      'inspect-brk',
      'inspect-port',
    ];
    for (const sw of prohibitedSwitches) {
      if (app.commandLine.hasSwitch(sw)) {
        console.error(
          `[SECURITY] Prohibited debugging switch '--${sw}' detected in packaged production mode. Terminating process immediately.`
        );
        app.exit(1);
        return;
      }
    }
  }
  loadPersistedSession();
  setupCustomProtocol();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// IPC Sender Validation Helper
function validateIpcSender(event: Electron.IpcMainInvokeEvent): void {
  if (!mainWindow || event.senderFrame !== mainWindow.webContents.mainFrame) {
    throw new Error('Unauthorized IPC sender frame');
  }
}

// IPC Handlers
ipcMain.handle('app:version', (event) => {
  validateIpcSender(event);
  return app.getVersion();
});

ipcMain.handle('app:platform', (event) => {
  validateIpcSender(event);
  return process.platform;
});

// Auth IPC Handlers:
// Main process handles login and token rotation directly.
// Refresh token is vaulted in safeStorage and NEVER sent to renderer.
ipcMain.handle('auth:login', async (event, credentials) => {
  validateIpcSender(event);
  if (!credentials || typeof credentials.email !== 'string' || typeof credentials.password !== 'string') {
    return { success: false, error: 'Invalid login parameters' };
  }

  try {
    const postData = JSON.stringify({
      email: credentials.email.trim(),
      password: credentials.password,
      tenant_slug: credentials.tenant_slug ? credentials.tenant_slug.trim() : undefined,
    });

    const response = await fetch(`${getBackendBaseUrl()}/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData).toString(),
      },
      body: postData,
    });

    if (!response.ok) {
      let errorDetail = 'Invalid email or password';
      try {
        const errJson: any = await response.json();
        if (errJson && errJson.detail) errorDetail = errJson.detail;
      } catch {}
      return { success: false, error: errorDetail, status: response.status };
    }

    const data: any = await response.json();

    // Fetch user profile
    const meRes = await fetch(`${getBackendBaseUrl()}/auth/me`, {
      headers: {
        Authorization: `Bearer ${data.access_token}`,
      },
    });

    if (!meRes.ok) {
      return { success: false, error: 'Failed to retrieve user profile' };
    }

    const userProfile: any = await meRes.json();

    // Vault refresh token strictly inside main process
    activeSession = {
      accessToken: data.access_token,
      refreshToken: data.refresh_token || null,
      user: userProfile,
    };
    persistSession();

    // Return ONLY accessToken and user to renderer (ZERO refresh token exposure)
    return {
      success: true,
      accessToken: data.access_token,
      user: userProfile,
    };
  } catch (err: any) {
    console.error('Login dispatch error in main process:', err);
    return { success: false, error: err?.message || 'Authentication connection failed' };
  }
});

ipcMain.handle('auth:get-session', (event) => {
  validateIpcSender(event);
  if (!activeSession) return { accessToken: null, user: null };
  return {
    accessToken: activeSession.accessToken,
    user: activeSession.user,
  };
});

ipcMain.handle('auth:clear-session', (event) => {
  validateIpcSender(event);
  activeSession = null;
  persistSession();
  return true;
});

// Concurrency-safe single-flight refresh lock in main process
ipcMain.handle('auth:refresh-token', async (event) => {
  validateIpcSender(event);
  if (!activeSession || !activeSession.refreshToken) {
    return null;
  }

  // Mutex: reuse in-flight refresh promise for concurrent callers
  if (refreshMutexPromise) {
    return refreshMutexPromise;
  }

  refreshMutexPromise = (async () => {
    try {
      if (!activeSession || !activeSession.refreshToken) {
        return null;
      }

      const postData = JSON.stringify({ refresh_token: activeSession.refreshToken });
      const response = await fetch(`${getBackendBaseUrl()}/auth/refresh`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(postData).toString(),
        },
        body: postData,
      });

      if (!response.ok) {
        activeSession = null;
        persistSession();
        return null;
      }

      const data: any = await response.json();
      activeSession.accessToken = data.access_token;
      if (data.refresh_token) {
        activeSession.refreshToken = data.refresh_token;
      }
      persistSession();

      // Return ONLY accessToken to renderer
      return {
        accessToken: data.access_token,
        expiresIn: data.expires_in,
      };
    } catch (err) {
      console.error('Failed to refresh token in main process:', err);
      return null;
    } finally {
      refreshMutexPromise = null;
    }
  })();

  return refreshMutexPromise;
});

ipcMain.handle('auth:logout', async (event) => {
  validateIpcSender(event);
  if (activeSession && activeSession.accessToken) {
    try {
      await fetch(`${getBackendBaseUrl()}/auth/logout`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${activeSession.accessToken}`,
        },
      });
    } catch {
      // Ignore network errors on logout
    }
  }
  activeSession = null;
  persistSession();
  return true;
});

// Media Download & Per-Account Cache IPC Handlers
ipcMain.handle('media:check-cache', (event, { artifactId }: { artifactId: string }) => {
  validateIpcSender(event);
  if (!activeSession || !activeSession.user) return { isCached: false };

  const safeArtifactId = artifactId.replace(/[^a-zA-Z0-9_-]/g, '');
  const userDir = getUserCacheDir(activeSession.user.tenant_id, activeSession.user.user_id);
  let targetDir = path.join(userDir, safeArtifactId);

  let found = fs.existsSync(targetDir);
  if (!found && (activeSession.user.role === 'owner' || activeSession.user.role === 'admin')) {
    const tenantDir = getTenantDir(activeSession.user.tenant_id);
    if (fs.existsSync(tenantDir)) {
      for (const uFolder of fs.readdirSync(tenantDir)) {
        const candidate = path.join(tenantDir, uFolder, safeArtifactId);
        if (fs.existsSync(candidate)) {
          targetDir = candidate;
          found = true;
          break;
        }
      }
    }
  }

  if (!found) return { isCached: false };

  const files = fs.readdirSync(targetDir).filter((f) => !f.endsWith('.part'));
  if (files.length === 0) return { isCached: false };

  const filePath = path.join(targetDir, files[0]);
  const stat = fs.statSync(filePath);
  if (stat.size === 0) return { isCached: false };

  return {
    isCached: true,
    mediaUrl: `mediaforge-media://playback/${safeArtifactId}`,
    sizeBytes: stat.size,
  };
});

ipcMain.handle('media:clear-cache', (event) => {
  validateIpcSender(event);
  try {
    const root = getCacheRoot();
    fs.rmSync(root, { recursive: true, force: true });
    fs.mkdirSync(root, { recursive: true });
    return true;
  } catch {
    return false;
  }
});

ipcMain.handle(
  'media:download',
  async (
    event,
    params: {
      artifactId: string;
      downloadUrl: string;
      filename: string;
      expectedSha256?: string;
    }
  ) => {
    validateIpcSender(event);
    if (!activeSession || !activeSession.user) {
      throw new Error('Authentication required for media caching');
    }

    const { artifactId, downloadUrl, filename, expectedSha256 } = params;

    // Validate origin
    const parsed = new URL(downloadUrl);
    const backendOrigin = new URL(getBackendBaseUrl()).origin;
    if (parsed.origin !== backendOrigin) {
      throw new Error('Unauthorized download source origin: ' + parsed.origin);
    }

    const safeArtifactId = artifactId.replace(/[^a-zA-Z0-9_-]/g, '');
    const userDir = getUserCacheDir(activeSession.user.tenant_id, activeSession.user.user_id);
    const targetDir = path.join(userDir, safeArtifactId);

    if (!fs.existsSync(targetDir)) {
      fs.mkdirSync(targetDir, { recursive: true });
    }

    const safeFilename = path.basename(filename);
    const finalPath = path.join(targetDir, safeFilename);
    const partPath = path.join(targetDir, `${safeFilename}.part`);

    if (fs.existsSync(partPath)) {
      fs.unlinkSync(partPath);
    }

    return new Promise((resolve, reject) => {
      const headers: Record<string, string> = {};
      if (activeSession && activeSession.accessToken) {
        headers['Authorization'] = `Bearer ${activeSession.accessToken}`;
      }

      const transport = parsed.protocol === 'https:' ? https : http;
      const req = transport.get(downloadUrl, { headers }, (res) => {
        if (res.statusCode !== 200) {
          return reject(new Error(`Server returned HTTP ${res.statusCode}: ${res.statusMessage}`));
        }

        const totalBytes = parseInt(res.headers['content-length'] || '0', 10);
        let bytesReceived = 0;
        const fileStream = fs.createWriteStream(partPath);
        const hash = crypto.createHash('sha256');

        res.on('data', (chunk: Buffer) => {
          bytesReceived += chunk.length;
          hash.update(chunk);
          fileStream.write(chunk);

          const percent = totalBytes > 0 ? Math.round((bytesReceived / totalBytes) * 100) : 0;
          if (mainWindow) {
            mainWindow.webContents.send('media:download-progress', {
              artifactId,
              percent,
              bytesReceived,
              totalBytes,
            });
          }
        });

        res.on('end', () => {
          fileStream.end(async () => {
            const calculatedHash = hash.digest('hex');

            // Verify SHA-256 Checksum if provided
            if (expectedSha256 && calculatedHash.toLowerCase() !== expectedSha256.toLowerCase()) {
              if (fs.existsSync(partPath)) fs.unlinkSync(partPath);
              return reject(
                new Error(
                  `SHA-256 verification failed! Expected ${expectedSha256}, got ${calculatedHash}`
                )
              );
            }

            // Atomic promotion
            if (fs.existsSync(finalPath)) {
              fs.unlinkSync(finalPath);
            }
            fs.renameSync(partPath, finalPath);

            resolve({
              success: true,
              mediaUrl: `mediaforge-media://playback/${safeArtifactId}`,
              filename: safeFilename,
            });
          });
        });

        res.on('error', (err) => {
          fileStream.destroy();
          if (fs.existsSync(partPath)) fs.unlinkSync(partPath);
          reject(err);
        });
      });

      req.on('error', (err) => {
        if (fs.existsSync(partPath)) fs.unlinkSync(partPath);
        reject(err);
      });
    });
  }
);

ipcMain.handle('shell:open-external', async (event, url: string) => {
  validateIpcSender(event);
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      await shell.openExternal(url);
      return true;
    }
  } catch {
    // Ignore invalid
  }
  return false;
});

// Backend Distribution Configuration & Testing IPC Handlers
ipcMain.handle('backend:get-config', (event) => {
  validateIpcSender(event);
  const currentUrl = getBackendBaseUrl();
  const parsed = new URL(currentUrl);
  const isLoopback =
    parsed.hostname === 'localhost' ||
    parsed.hostname === '127.0.0.1' ||
    parsed.hostname === '::1';
  return {
    url: currentUrl,
    isLoopback,
    isHttps: parsed.protocol === 'https:',
  };
});

ipcMain.handle('backend:set-url', (event, newUrl: string) => {
  validateIpcSender(event);
  if (typeof newUrl !== 'string') {
    return { success: false, error: 'Invalid URL format' };
  }
  const trimmed = newUrl.trim().replace(/\/+$/, '');
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return { success: false, error: 'Malformed URL' };
  }

  const isLoopback =
    parsed.hostname === 'localhost' ||
    parsed.hostname === '127.0.0.1' ||
    parsed.hostname === '::1';

  if (parsed.protocol === 'http:' && !isLoopback) {
    return {
      success: false,
      error: 'Insecure HTTP is strictly prohibited for remote backends. HTTPS is mandatory.',
    };
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return { success: false, error: 'Protocol must be HTTP or HTTPS.' };
  }

  const oldOrigin = new URL(getBackendBaseUrl()).origin;
  const newOrigin = parsed.origin;
  let sessionInvalidated = false;

  if (oldOrigin !== newOrigin) {
    // Invalidate active session and vault when switching backend origin
    activeSession = null;
    sessionInvalidated = true;
    try {
      const vaultPath = getSessionFilePath();
      if (fs.existsSync(vaultPath)) fs.unlinkSync(vaultPath);
    } catch {}
  }

  customBackendUrl = trimmed;
  return {
    success: true,
    url: trimmed,
    sessionInvalidated,
  };
});

ipcMain.handle('backend:test-connection', async (event, testUrl?: string) => {
  validateIpcSender(event);
  const targetUrl = testUrl && typeof testUrl === 'string' ? testUrl.trim().replace(/\/+$/, '') : getBackendBaseUrl();
  const startTime = Date.now();
  try {
    const healthUrl = `${targetUrl}/health`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    const res = await fetch(healthUrl, { method: 'GET', signal: controller.signal });
    clearTimeout(timeout);
    const latencyMs = Date.now() - startTime;
    let data: any = null;
    try {
      data = await res.json();
    } catch {}
    return {
      online: res.ok,
      status: res.status,
      latencyMs,
      version: data?.version,
    };
  } catch (err: any) {
    return {
      online: false,
      status: 0,
      latencyMs: Date.now() - startTime,
      error: err.message || 'Connection failed',
    };
  }
});
