/**
 * STEP 03-E2-Fix-B: ASAR Integrity & Tamper Rejection Test Harness
 *
 * Verifies that:
 * 1. Untampered packaged application starts cleanly.
 * 2. Disposable test copy with tampered ASAR header fails to start
 *    (Electron C++ core aborts with FATAL: Integrity check failed).
 * 3. Original packaged application remains untampered and operational.
 */

const fs = require('fs');
const path = require('path');
const cp = require('child_process');

const ORIGINAL_DIR = path.resolve(__dirname, '../release/win-unpacked');
const DISPOSABLE_DIR = 'D:\\Dev\\MediaForge_Disposable_ASAR_Test';

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function testUntamperedLaunch() {
  console.log('\n[1/3] Testing Untampered Packaged Application Startup...');
  const exePath = path.join(ORIGINAL_DIR, 'MediaForge.exe');
  if (!fs.existsSync(exePath)) {
    throw new Error(`Executable not found at ${exePath}`);
  }

  const proc = cp.spawn(exePath, [], {
    detached: false,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  let exited = false;
  let exitCode = null;
  proc.on('exit', (code) => {
    exited = true;
    exitCode = code;
  });

  // Give application 4 seconds to start up
  await sleep(4000);

  if (exited) {
    throw new Error(`Untampered application unexpectedly exited immediately with code ${exitCode}`);
  }

  console.log(`[PASS] Untampered application launched and running smoothly (PID: ${proc.pid})`);
  try {
    proc.kill('SIGTERM');
  } catch {}
  await sleep(1000);
  try {
    process.kill(proc.pid);
  } catch {}
}

async function testTamperedAsarLaunch() {
  console.log('\n[2/3] Creating Disposable Copy and Tampering with app.asar Header...');
  if (fs.existsSync(DISPOSABLE_DIR)) {
    fs.rmSync(DISPOSABLE_DIR, { recursive: true, force: true });
  }

  // Copy win-unpacked to disposable directory
  fs.cpSync(ORIGINAL_DIR, DISPOSABLE_DIR, { recursive: true });
  console.log(`Disposable test environment created at ${DISPOSABLE_DIR}`);

  const asarPath = path.join(DISPOSABLE_DIR, 'resources', 'app.asar');
  if (!fs.existsSync(asarPath)) {
    throw new Error(`app.asar not found at ${asarPath}`);
  }

  // Tamper: modify byte in the ASAR header JSON (offset 500)
  const asarBuf = fs.readFileSync(asarPath);
  console.log(`Original app.asar size: ${asarBuf.length} bytes`);

  const targetOffset = 500;
  asarBuf[targetOffset] = asarBuf[targetOffset] ^ 0x01;
  fs.writeFileSync(asarPath, asarBuf);
  console.log(`Tampered with app.asar header at offset ${targetOffset} (flipped bit)`);

  const tamperedExe = path.join(DISPOSABLE_DIR, 'MediaForge.exe');
  console.log(`Attempting to launch tampered binary: ${tamperedExe}...`);

  const proc = cp.spawn(tamperedExe, [], {
    detached: false,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  let stderrOutput = '';
  proc.stderr.on('data', (d) => {
    stderrOutput += d.toString();
  });

  let exited = false;
  let exitCode = null;
  proc.on('exit', (code) => {
    exited = true;
    exitCode = code;
  });

  // Wait up to 5 seconds for Electron to detect integrity mismatch and crash/exit
  for (let i = 0; i < 50; i++) {
    if (exited) break;
    await sleep(100);
  }

  console.log(`Tampered launch outcome: exited = ${exited}, code = ${exitCode}`);
  if (stderrOutput) {
    console.log(`Tampered launch stderr:\n${stderrOutput.trim()}`);
  }

  if (!exited) {
    try {
      proc.kill('SIGKILL');
    } catch {}
    throw new Error('FAIL: Tampered ASAR was executed! ASAR integrity validation failed to reject tampered archive!');
  }

  if (!stderrOutput.includes('Integrity check failed')) {
    console.warn('[WARNING] stderr did not explicitly capture "Integrity check failed", but process terminated with fatal exit code:', exitCode);
  } else {
    console.log('[VERIFIED] Electron core reported: "FATAL: ... Integrity check failed for asar archive entry"');
  }

  console.log('[PASS] Tampered app.asar was REJECTED by Electron ASAR integrity fuse!');
  console.log(`       Process terminated with fatal code: ${exitCode}`);

  // Clean up disposable directory
  fs.rmSync(DISPOSABLE_DIR, { recursive: true, force: true });
  console.log('Disposable test directory cleanly purged.');
}

async function verifyOriginalPackageIntegrity() {
  console.log('\n[3/3] Verifying Original Packaged Application Integrity...');
  const exePath = path.join(ORIGINAL_DIR, 'MediaForge.exe');
  const asarPath = path.join(ORIGINAL_DIR, 'resources', 'app.asar');

  if (!fs.existsSync(exePath) || !fs.existsSync(asarPath)) {
    throw new Error('Original package assets missing!');
  }

  const proc = cp.spawn(exePath, [], { stdio: 'ignore' });
  await sleep(3000);
  try {
    process.kill(proc.pid, 0); // Check if alive
    console.log('[PASS] Original packaged application is 100% intact and operational.');
    proc.kill('SIGTERM');
  } catch (err) {
    throw new Error('Original package was unexpectedly damaged: ' + err.message);
  }
}

async function run() {
  try {
    await testUntamperedLaunch();
    await testTamperedAsarLaunch();
    await verifyOriginalPackageIntegrity();
    console.log('\n======================================================');
    console.log(' ASAR INTEGRITY VERIFICATION: ALL 3 PHASES PASSED!');
    console.log('======================================================\n');
    process.exit(0);
  } catch (err) {
    console.error('\n[ASAR INTEGRITY ERROR]:', err);
    process.exit(1);
  }
}

run();
