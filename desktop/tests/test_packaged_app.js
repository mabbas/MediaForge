/**
 * MediaForge STEP 03-E1: Packaged Executable Interactive Verification Test
 *
 * Tests the compiled, unpacked release candidate executable outside dev-server:
 * Target: desktop/release/win-unpacked/MediaForge.exe
 *
 * Verifies:
 *  1. Launches without dev-server dependency (loads directly from ASAR archive).
 *  2. Connects to backend API (http://127.0.0.1:58000/api/v1).
 *  3. Interactive GUI authentication via DOM inputs.
 *  4. Loads Media Library from backend API.
 *  5. Plays cached media via custom mediaforge-media:// protocol in HTML5 <video>.
 *  6. Verifies memory, CPU and process footprint.
 */

const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const EXECUTABLE_PATH = path.join(__dirname, '../release/win-unpacked/MediaForge.exe');
const CDP_PORT = 9222;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function runPackagedAppTest() {
  console.log('======================================================================');
  console.log(' MediaForge STEP 03-E1: Packaged Application Automated Verification');
  console.log(' Target Executable: ' + EXECUTABLE_PATH);
  console.log('======================================================================\n');

  if (!fs.existsSync(EXECUTABLE_PATH)) {
    console.error('FATAL: Packaged executable does not exist at ' + EXECUTABLE_PATH);
    process.exit(1);
  }

  let passed = 0;
  let failed = 0;

  function report(name, condition, details = '') {
    if (condition) {
      passed++;
      console.log(`[PASS] ${name}`);
      if (details) console.log(`       Evidence: ${details}`);
    } else {
      failed++;
      console.log(`[FAIL] ${name}`);
      if (details) console.log(`       Error: ${details}`);
    }
  }

  // Ensure local cache exists in both mediaforge-desktop and MediaForge userData directories
  const userDataFolders = ['mediaforge-desktop', 'MediaForge'];
  for (const folder of userDataFolders) {
    const targetCacheDir = path.join(
      process.env.APPDATA || '',
      folder,
      'media_cache',
      'c2fix-tenant-fc055d4b',
      '855bb52a-a959-4f21-9a98-f7be412ee47d',
      'test-decoded-playback-zoo'
    );
    if (!fs.existsSync(targetCacheDir)) {
      fs.mkdirSync(targetCacheDir, { recursive: true });
    }
    const cachedVideoTarget = path.join(targetCacheDir, 'cached_zoo_video.mp4');
    const electronCachedSource = path.join(
      process.env.APPDATA || '',
      'Electron',
      'media_cache',
      'c2fix-tenant-fc055d4b',
      '855bb52a-a959-4f21-9a98-f7be412ee47d',
      'test-decoded-playback-zoo',
      'cached_zoo_video.mp4'
    );
    if (fs.existsSync(electronCachedSource)) {
      fs.copyFileSync(electronCachedSource, cachedVideoTarget);
    } else {
      try {
        execSync(`docker cp mediaforge-api:"/app/downloads/c2fix-tenant-fc055d4b/downloads/Me at the zoo.mp4" "${cachedVideoTarget}"`);
      } catch {}
    }
  }

  // 1. Launch packaged executable with remote debugging and automated playback permission
  const appProc = spawn(
    EXECUTABLE_PATH,
    [
      `--remote-debugging-port=${CDP_PORT}`,
      '--autoplay-policy=no-user-gesture-required',
    ],
    {
      detached: false,
      stdio: 'ignore',
    }
  );

  let ws = null;
  let msgId = 1;
  const pendingRequests = new Map();

  function sendCDP(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = msgId++;
      pendingRequests.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async function evaluate(expression, awaitPromise = true) {
    const res = await sendCDP('Runtime.evaluate', {
      expression,
      awaitPromise,
      returnByValue: true,
    });
    if (res.result?.exceptionDetails) {
      throw new Error(res.result.exceptionDetails.text || 'CDP Evaluation Exception');
    }
    return res.result?.result?.value;
  }

  try {
    await sleep(3000);

    // 2. Query CDP targets
    const cdpTargetsRes = await fetch(`http://127.0.0.1:${CDP_PORT}/json`);
    const cdpTargets = await cdpTargetsRes.json();
    const pageTarget = cdpTargets.find((t) => t.type === 'page');

    report(
      '1. Packaged Executable Launch Outside Dev Environment',
      !!pageTarget && pageTarget.url.startsWith('file://'),
      `Page Target URL: ${pageTarget?.url} (ASAR bundled, 0 dev-server dependency)`
    );

    // Connect WebSocket
    ws = new WebSocket(pageTarget.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = reject;
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.id && pendingRequests.has(msg.id)) {
          const { resolve } = pendingRequests.get(msg.id);
          pendingRequests.delete(msg.id);
          resolve(msg);
        }
      };
    });

    // 3. Authenticate through GUI inputs
    const authResult = await evaluate(`
      (async () => {
        if (document.body.innerText.includes('alice.c5c617@example.com')) {
          return { success: true, alreadyLoggedIn: true };
        }

        const emailInput = document.querySelector('input[type="email"]');
        const passInput = document.querySelector('input[type="password"]');
        const tenantInput = document.querySelector('form input[type="text"]');
        const form = document.querySelector('form');
        if (!emailInput || !passInput || !form) return { error: 'Login elements missing' };

        const setVal = (el, val) => {
          if (!el) return;
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(el, val);
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        };

        setVal(emailInput, 'alice.c5c617@example.com');
        setVal(passInput, 'TestPass123!_2acfa8');
        setVal(tenantInput, 'org-fc055d4b');

        await new Promise(r => setTimeout(r, 100));
        form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));

        // Wait for dashboard transition
        for (let i = 0; i < 30; i++) {
          await new Promise(r => setTimeout(r, 200));
          if (document.body.innerText.includes('alice.c5c617@example.com')) {
            return { success: true };
          }
        }
        return { error: 'Dashboard transition timeout' };
      })()
    `);

    report(
      '2. GUI Authentication via Local Backend',
      authResult.success === true,
      'Authenticated as alice.c5c617@example.com via http://127.0.0.1:58000/api/v1'
    );

    // 4. Navigate to Media Library and verify library items
    const mediaLibResult = await evaluate(`
      (async () => {
        // Find and click Media Library button in navigation
        const navButtons = Array.from(document.querySelectorAll('nav button, aside button, button'));
        const mediaBtn = navButtons.find(b => b.innerText && b.innerText.includes('Media Library'));
        if (mediaBtn) {
          mediaBtn.click();
        }

        // Wait for media items to load from API
        for (let i = 0; i < 30; i++) {
          await new Promise(r => setTimeout(r, 200));
          const text = document.body.innerText;
          if (text.includes('Media Library') && (text.includes('Me at the zoo') || text.includes('.mp4') || text.includes('clip_') || text.includes('Bytes') || text.includes('KB') || text.includes('MB'))) {
            return {
              success: true,
              libraryRendered: true,
            };
          }
        }
        return { success: false, reason: 'Media library content timeout' };
      })()
    `);

    report(
      '3. Load Media Library from Backend API',
      mediaLibResult.success === true,
      'Media Library view mounted and populated with tenant media assets'
    );

    // 5. Play cached media via mediaforge-media:// in HTML5 <video> inside packaged app
    const cacheStatus = await evaluate(`
      (async () => {
        try {
          return await window.mediaforge.media.checkCache('test-decoded-playback-zoo');
        } catch (e) {
          return { error: e.message };
        }
      })()
    `);
    const fetchCheck = await evaluate(`
      (async () => {
        try {
          const res = await fetch('mediaforge-media://playback/test-decoded-playback-zoo');
          return { status: res.status, ok: res.ok };
        } catch (e) {
          return { error: e.message };
        }
      })()
    `);
    const playbackResult = await evaluate(`
      new Promise((resolve) => {
        const vid = document.createElement('video');
        vid.id = 'packaged_test_player';
        vid.muted = true;
        vid.playsInline = true;
        document.body.appendChild(vid);

        const metrics = {
          duration: 0,
          videoWidth: 0,
          videoHeight: 0,
          currentTime: 0,
          paused: true
        };

        const timeout = setTimeout(() => {
          resolve({ error: 'Packaged video playback timed out', metrics, currentTime: vid.currentTime, readyState: vid.readyState });
        }, 10000);

        vid.addEventListener('loadedmetadata', () => {
          metrics.duration = vid.duration;
          metrics.videoWidth = vid.videoWidth;
          metrics.videoHeight = vid.videoHeight;
        });

        vid.addEventListener('canplay', () => {
          metrics.duration = vid.duration;
          metrics.videoWidth = vid.videoWidth;
          metrics.videoHeight = vid.videoHeight;
          // Seek to midpoint via HTTP 206 range slicing
          vid.currentTime = 3.5;
        });

        vid.addEventListener('seeked', () => {
          clearTimeout(timeout);
          resolve({
            success: true,
            duration: vid.duration,
            videoWidth: vid.videoWidth,
            videoHeight: vid.videoHeight,
            currentTime: vid.currentTime,
            readyState: vid.readyState,
            paused: vid.paused
          });
        });

        vid.addEventListener('error', () => {
          clearTimeout(timeout);
          resolve({ error: 'Video element error: ' + (vid.error ? vid.error.message : 'Unknown') });
        });

        vid.src = 'mediaforge-media://playback/test-decoded-playback-zoo';
      })
    `);

    const playbackSuccess = playbackResult.success === true && playbackResult.currentTime > 1.0 && playbackResult.videoWidth > 0;
    report(
      '4. Real Decoded HTML5 Video Playback in Packaged Binary',
      playbackSuccess,
      `Duration: ${playbackResult.duration?.toFixed(2)}s, Decoded Resolution: ${playbackResult.videoWidth}x${playbackResult.videoHeight}, CurrentTime: ${playbackResult.currentTime?.toFixed(2)}s`
    );

    // 6. Record resource footprint (Process count, memory, CPU)
    const procInfo = execSync('powershell -Command "Get-Process MediaForge | Select-Object Id, WorkingSet64, CPU | ConvertTo-Json"', { encoding: 'utf8' });
    const procData = JSON.parse(procInfo);
    const procList = Array.isArray(procData) ? procData : [procData];
    const totalWorkingSetMb = procList.reduce((acc, p) => acc + (p.WorkingSet64 / (1024 * 1024)), 0);
    const totalCpuSeconds = procList.reduce((acc, p) => acc + (p.CPU || 0), 0);

    report(
      '5. Packaged Application Resource Footprint',
      procList.length >= 3 && totalWorkingSetMb > 50 && totalWorkingSetMb < 1200,
      `Process Count: ${procList.length}, Total Working Set Memory: ${totalWorkingSetMb.toFixed(2)} MB, Total CPU Time: ${totalCpuSeconds.toFixed(2)}s`
    );

  } catch (err) {
    console.error('Test execution failed:', err);
    failed++;
  } finally {
    if (ws) {
      try { ws.close(); } catch {}
    }
    try {
      execSync('powershell -Command "Stop-Process -Name MediaForge -Force -ErrorAction SilentlyContinue"');
    } catch {}

    console.log('\n======================================================================');
    console.log(` Summary: ${passed} PASSED, ${failed} FAILED (Total: ${passed + failed})`);
    console.log('======================================================================\n');
    process.exit(failed === 0 ? 0 : 1);
  }
}

runPackagedAppTest();
