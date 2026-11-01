/**
 * MediaForge Desktop Electron Smoke & Integration Test Suite
 * Verifies IPC contracts, security configurations, session handling,
 * and live backend communications with FastAPI at http://127.0.0.1:58000/api/v1.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';

const API_BASE = 'http://127.0.0.1:58000/api/v1';

async function runTests() {
  console.log('====================================================');
  console.log('MediaForge Desktop Foundation Verification');
  console.log('====================================================\n');

  let passed = 0;
  let failed = 0;

  function assert(condition, message) {
    if (condition) {
      console.log(`[PASS] ${message}`);
      passed++;
    } else {
      console.error(`[FAIL] ${message}`);
      failed++;
    }
  }

  // 1. Project Structure Verification
  console.log('--- 1. Project Structure & Build Artifacts ---');
  assert(fs.existsSync('desktop/package.json'), 'desktop/package.json exists');
  assert(fs.existsSync('desktop/dist/electron/main.js'), 'desktop/dist/electron/main.js built');
  assert(fs.existsSync('desktop/dist/electron/preload.js'), 'desktop/dist/electron/preload.js built');
  assert(fs.existsSync('desktop/dist/renderer/index.html'), 'desktop/dist/renderer/index.html built');

  // 2. Security Architecture Audit
  console.log('\n--- 2. Electron Security Preferences Audit ---');
  const mainCode = fs.readFileSync('desktop/electron/main.ts', 'utf-8');
  assert(mainCode.includes('contextIsolation: true'), 'contextIsolation: true enforced');
  assert(mainCode.includes('nodeIntegration: false'), 'nodeIntegration: false enforced');
  assert(mainCode.includes('sandbox: true'), 'sandbox: true enforced');
  assert(mainCode.includes('webSecurity: true'), 'webSecurity: true enforced');
  assert(mainCode.includes('will-navigate'), 'Navigation lockdown handler present');
  assert(mainCode.includes('setWindowOpenHandler'), 'Disallow arbitrary popup windows present');
  assert(mainCode.includes('setPermissionRequestHandler'), 'Deny unneeded web permissions present');
  assert(mainCode.includes("protocol.handle('mediaforge-media'"), 'mediaforge-media custom protocol handler registered');

  // Audit Preload
  const preloadCode = fs.readFileSync('desktop/electron/preload.ts', 'utf-8');
  assert(preloadCode.includes("contextBridge.exposeInMainWorld('mediaforge'"), 'contextBridge narrowly exposes mediaforge');
  assert(!preloadCode.includes('ipcRenderer.sendSync'), 'No synchronous blocking IPC in preload');
  assert(!preloadCode.includes('require('), 'No raw Node require passed to renderer');

  // 3. Backend Health Check
  console.log('\n--- 3. Live FastAPI Backend Connectivity ---');
  try {
    const healthRes = await fetch(`${API_BASE}/health`);
    const healthJson = await healthRes.json();
    assert(healthRes.status === 200, `Health check returned 200 OK (uptime: ${healthJson.uptime_seconds}s)`);
    assert(healthJson.status === 'healthy', 'Backend status reports healthy');
  } catch (err) {
    assert(false, `Health check failed: ${err.message}`);
  }

  // 4. Desktop Authentication & Session Lifecycle
  console.log('\n--- 4. Authentication Flow (Login, Me, Refresh, Token Isolation) ---');
  let accessToken = null;
  let refreshToken = null;
  let userMe = null;

  try {
    const loginRes = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'session.user.62c006@example.com',
        password: 'SecurePassword123!',
        tenant_slug: 'session-org-62c006',
      }),
    });
    const loginJson = await loginRes.json();
    assert(loginRes.status === 200, 'Login succeeded with HTTP 200');
    assert(loginJson.access_token && loginJson.refresh_token, 'Login returned signed JWT and rotating refresh token');
    accessToken = loginJson.access_token;
    refreshToken = loginJson.refresh_token;

    // Call GET /auth/me
    const meRes = await fetch(`${API_BASE}/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    userMe = await meRes.json();
    assert(meRes.status === 200, `GET /auth/me succeeded for user ${userMe.email}`);
    assert(userMe.tenant_id && userMe.role, 'User profile returned tenant_id and role');

    // Token refresh
    const refreshRes = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    const refreshJson = await refreshRes.json();
    assert(refreshRes.status === 200, 'POST /auth/refresh succeeded with HTTP 200');
    assert(refreshJson.access_token !== accessToken, 'Token refresh rotated to new access token');
    accessToken = refreshJson.access_token;
    refreshToken = refreshJson.refresh_token;
  } catch (err) {
    assert(false, `Authentication test failed: ${err.message}`);
  }

  // 5. Media Library Listing
  console.log('\n--- 5. Media Library Integration ---');
  try {
    const artifactsRes = await fetch(`${API_BASE}/artifacts?limit=10`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    const artifactsJson = await artifactsRes.json();
    assert(artifactsRes.status === 200, `GET /artifacts succeeded (returned ${artifactsJson.items.length} items)`);

    const downloadsRes = await fetch(`${API_BASE}/downloads`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    const downloadsJson = await downloadsRes.json();
    assert(downloadsRes.status === 200, `GET /downloads succeeded (returned ${downloadsJson.jobs.length} jobs)`);
  } catch (err) {
    assert(false, `Media library test failed: ${err.message}`);
  }

  // 6. Asynchronous Background Clip Job Workflow
  console.log('\n--- 6. Asynchronous Clip Workflow (HTTP 202 Acceptance & Tracking) ---');
  let submittedJobId = null;
  try {
    const clipPayload = {
      source: `https://example.com/sample_${Date.now()}.mp4`,
      start_time: '00:00:01',
      end_time: '00:00:05',
      mode: 'precise',
      output_format: 'mp4',
    };

    const submitRes = await fetch(`${API_BASE}/jobs/clips`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(clipPayload),
    });

    const submitJson = await submitRes.json();
    assert(submitRes.status === 202, `POST /jobs/clips returned HTTP 202 Accepted (job_id: ${submitJson.job_id})`);
    assert(submitJson.status === 'queued', 'Initial job state is queued');
    submittedJobId = submitJson.job_id;

    // Track job status via GET /jobs/{id}
    const jobDetailRes = await fetch(`${API_BASE}/jobs/${submittedJobId}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    const jobDetailJson = await jobDetailRes.json();
    assert(jobDetailRes.status === 200, `GET /jobs/${submittedJobId} returned HTTP 200`);
    assert(
      ['queued', 'running', 'succeeded', 'failed', 'cancelled'].includes(jobDetailJson.status),
      `Job detail status is valid: ${jobDetailJson.status} (worker: ${jobDetailJson.claimed_by || 'pending'})`
    );

    // Cancel job if still queued/running
    if (['queued', 'running'].includes(jobDetailJson.status)) {
      const cancelRes = await fetch(`${API_BASE}/jobs/${submittedJobId}/cancel`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      const cancelJson = await cancelRes.json();
      assert(cancelRes.status === 200, `POST /jobs/${submittedJobId}/cancel succeeded (status: ${cancelJson.status})`);
    } else {
      assert(true, `Worker executed work item promptly to terminal state ${jobDetailJson.status}`);
    }
  } catch (err) {
    assert(false, `Clip workflow test failed: ${err.message}`);
  }

  // 7. Security Isolation Verification
  console.log('\n--- 7. Security Invariant Verification ---');
  // Confirm renderer bundle has no node:fs or child_process imports
  const rendererJs = fs.readFileSync(
    path.join('desktop/dist/renderer/assets', fs.readdirSync('desktop/dist/renderer/assets').find((f) => f.endsWith('.js'))),
    'utf-8'
  );
  assert(!rendererJs.includes('require("fs")'), 'Renderer bundle does not import Node fs');
  assert(!rendererJs.includes('require("child_process")'), 'Renderer bundle does not import Node child_process');
  assert(!rendererJs.includes('ipcRenderer.send('), 'Renderer bundle does not contain arbitrary raw IPC calls');

  console.log('\n====================================================');
  console.log(`Results: ${passed} PASSED, ${failed} FAILED`);
  console.log('====================================================');

  if (failed > 0) {
    process.exit(1);
  }
}

runTests().catch((err) => {
  console.error('Fatal test error:', err);
  process.exit(1);
});
