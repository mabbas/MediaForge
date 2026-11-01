/**
 * MediaForge STEP 03-E2-Fix-A: Hostile Production Startup Lockdown Acceptance Test
 *
 * Verifies that the packaged production application strictly enforces:
 *  1. Passing --remote-debugging-port terminates process immediately (exit code 1) and opens ZERO listener.
 *  2. Setting MEDIAFORGE_TEST_E2E=1 or MEDIAFORGE_DEBUG_PORT does NOT enable debugging.
 *  3. Setting NODE_OPTIONS="--inspect=127.0.0.1:9229" is completely ignored by fuse (zero listener).
 *  4. Setting ELECTRON_RUN_AS_NODE=1 is rejected by fuse (does not execute as Node).
 *  5. Passing --inspect is ignored or rejected by fuse.
 *  6. Normal launch without debugging flags starts cleanly with zero debug ports open.
 */

const { spawn } = require('child_process');
const path = require('path');
const net = require('net');

const EXE_PATH = path.resolve(__dirname, '../release/win-unpacked/MediaForge.exe');

function checkPort(port, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let status = 'closed';

    socket.setTimeout(timeoutMs);
    socket.on('connect', () => {
      status = 'open';
      socket.destroy();
    });
    socket.on('timeout', () => {
      socket.destroy();
    });
    socket.on('error', () => {
      // Connection refused -> port is closed
    });
    socket.on('close', () => {
      resolve(status === 'open');
    });

    socket.connect(port, '127.0.0.1');
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function runHostileStartupTests() {
  console.log('======================================================================');
  console.log(' MediaForge STEP 03-E2-Fix-A: Hostile Production Startup Lockdown Test');
  console.log(' Target Executable: ' + EXE_PATH);
  console.log('======================================================================\n');

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

  // ── TEST 1: Attempt launch with --remote-debugging-port ───────────────────
  console.log('[1/5] Testing launch with --remote-debugging-port=59998...');
  const proc1 = spawn(EXE_PATH, ['--remote-debugging-port=59998'], {
    env: { ...process.env, MEDIAFORGE_TEST_E2E: '1', MEDIAFORGE_DEBUG_PORT: '59998' },
    stdio: 'pipe',
  });

  let proc1ExitCode = null;
  proc1.on('exit', (code) => {
    proc1ExitCode = code;
  });

  await sleep(2000);
  const port59998Open = await checkPort(59998);

  report(
    'Hostile --remote-debugging-port Rejected & Process Terminated',
    proc1ExitCode === 1 && !port59998Open,
    `Process exited with code ${proc1ExitCode}; Port 59998 open: ${port59998Open}`
  );
  try { proc1.kill(); } catch {}

  // ── TEST 2: Attempt NODE_OPTIONS inspector injection ─────────────────────
  console.log('\n[2/5] Testing launch with NODE_OPTIONS="--inspect=127.0.0.1:9229"...');
  const proc2 = spawn(EXE_PATH, [], {
    env: { ...process.env, NODE_OPTIONS: '--inspect=127.0.0.1:9229' },
    stdio: 'pipe',
  });

  await sleep(2500);
  const port9229Open = await checkPort(9229);

  report(
    'NODE_OPTIONS Inspector Injection Defeated by Fuse',
    !port9229Open,
    `Port 9229 open: ${port9229Open} (NodeOptions fuse disabled)`
  );
  try { proc2.kill(); } catch {}

  // ── TEST 3: Attempt ELECTRON_RUN_AS_NODE execution ────────────────────────
  console.log('\n[3/5] Testing ELECTRON_RUN_AS_NODE=1 execution...');
  const proc3 = spawn(EXE_PATH, ['-e', 'console.log("PWNED_AS_NODE")'], {
    env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
    stdio: 'pipe',
  });

  let proc3Output = '';
  proc3.stdout.on('data', (d) => proc3Output += d.toString());
  proc3.stderr.on('data', (d) => proc3Output += d.toString());

  await sleep(2000);
  report(
    'ELECTRON_RUN_AS_NODE Defeated by Fuse',
    !proc3Output.includes('PWNED_AS_NODE'),
    `Process output: "${proc3Output.trim()}" (RunAsNode fuse disabled)`
  );
  try { proc3.kill(); } catch {}

  // ── TEST 4: Attempt CLI --inspect flag ───────────────────────────────────
  console.log('\n[4/5] Testing CLI --inspect flag...');
  const proc4 = spawn(EXE_PATH, ['--inspect=9230'], {
    stdio: 'pipe',
  });

  await sleep(2000);
  const port9230Open = await checkPort(9230);
  report(
    'CLI --inspect Defeated by Fuse & Main Switch Guard',
    !port9230Open,
    `Port 9230 open: ${port9230Open}`
  );
  try { proc4.kill(); } catch {}

  // ── TEST 5: Legitimate Normal Launch (Zero debug ports open) ───────────────
  console.log('\n[5/5] Testing legitimate production launch (clean environment)...');
  const cleanEnv = { ...process.env };
  delete cleanEnv.NODE_OPTIONS;
  delete cleanEnv.ELECTRON_RUN_AS_NODE;
  delete cleanEnv.MEDIAFORGE_TEST_E2E;
  delete cleanEnv.MEDIAFORGE_DEBUG_PORT;

  const proc5 = spawn(EXE_PATH, [], {
    env: cleanEnv,
    stdio: 'pipe',
  });

  await sleep(3000);
  const normalPortCheck = (await checkPort(59998)) || (await checkPort(9229)) || (await checkPort(9230));
  const isRunning = proc5.exitCode === null;

  report(
    'Normal Packaged Launch Succeeds with Zero Open Debug Ports',
    isRunning && !normalPortCheck,
    `Application running: ${isRunning}, Any debug ports open: ${normalPortCheck}`
  );
  try { proc5.kill(); } catch {}

  console.log('\n======================================================================');
  console.log(` Summary: ${passed} PASSED, ${failed} FAILED (Total: ${passed + failed})`);
  console.log('======================================================================');

  process.exit(failed > 0 ? 1 : 0);
}

runHostileStartupTests().catch((err) => {
  console.error('Test error:', err);
  process.exit(1);
});
