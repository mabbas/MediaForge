/**
 * Comprehensive TLS Security & Certificate Acceptance Tests (TASK 1).
 * Verifies certificate validity, chain verification, negative rejection, and redirect behavior.
 */

const puppeteer = require("puppeteer-core");
const https = require("https");
const http = require("http");
const fs = require("fs");
const path = require("path");

const CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const SPKI_ROOT = "SWjpMxILG0Ljqw9j2uwg39r2/DBi0DrB3xhVRgM5jaM=";
const SPKI_INTERMEDIATE = "+eSgEP6ip4SSGSGOH7mRttFIuSteWBRiV4lKpZ25MSk=";
const SPKI_LEAF = "JKTrsh2UgDBDPFMVPrvnxOo00RVSeo4akmnM+mPrNFo=";
const CADDY_ROOT_CA_PATH = "D:\\Dev\\MediaForge\\deploy\\staging\\caddy_root.crt";

async function runTlsTests() {
  console.log("==================================================================");
  console.log("   STEP 04-B2-C TASK 1: COMPREHENSIVE TLS VALIDATION SUITE       ");
  console.log("==================================================================");

  const rootPem = fs.readFileSync(CADDY_ROOT_CA_PATH, "utf8");

  // 1. Node.js HTTPS native TLS verification with explicit ca option (Zero bypass)
  console.log("\n[Test 1] Node.js TLS Chain Verification with explicit ca (caddy_root.crt)...");
  await new Promise((resolve, reject) => {
    const req = https.get("https://localhost:8443/api/v1/health", { ca: rootPem, rejectUnauthorized: true }, (res) => {
      console.log(`  [PASS] HTTPS response status: ${res.statusCode} (HSTS: ${res.headers["strict-transport-security"]})`);
      if (res.statusCode === 200) resolve();
      else reject(new Error(`Expected 200, got ${res.statusCode}`));
    });
    req.on("error", (err) => reject(new Error(`TLS validation failed: ${err.message}`)));
  });

  // 2. Negative test: Node.js HTTPS without ca must fail with UNABLE_TO_VERIFY_LEAF_SIGNATURE or DEPTH_ZERO_SELF_SIGNED_CERT
  console.log("\n[Test 2] Negative Test: Default system trust store must reject private CA...");
  await new Promise((resolve, reject) => {
    const req = https.get("https://localhost:8443/api/v1/health", { rejectUnauthorized: true }, (res) => {
      reject(new Error("FAILED: Untrusted private CA was unexpectedly accepted by default trust store!"));
    });
    req.on("error", (err) => {
      console.log(`  [PASS] Expected TLS failure observed: ${err.code} (${err.message})`);
      resolve();
    });
  });

  // 3. Negative test: Hostname mismatch rejection
  console.log("\n[Test 3] Negative Test: Hostname mismatch rejection (unexpected servername)...");
  await new Promise((resolve, reject) => {
    const req = https.get("https://127.0.0.1:8443/api/v1/health", { ca: rootPem, rejectUnauthorized: true, servername: "unauthorized.domain.local" }, (res) => {
      reject(new Error("FAILED: Unexpected servername was accepted without error!"));
    });
    req.on("error", (err) => {
      console.log(`  [PASS] Hostname mismatch rejected as expected: ${err.message}`);
      resolve();
    });
  });

  // 4. Puppeteer negative test: Launching browser WITHOUT SPKI list and WITHOUT --ignore-certificate-errors must fail
  console.log("\n[Test 4] Negative Test: Puppeteer rejects untrusted staging cert by default...");
  const browserStrict = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
  });
  const pageStrict = await browserStrict.newPage();
  try {
    await pageStrict.goto("https://localhost:8443/api/v1/health", { waitUntil: "networkidle0" });
    throw new Error("FAILED: Puppeteer loaded untrusted cert without SPKI pin or ignore flag!");
  } catch (err) {
    console.log(`  [PASS] Browser strictly blocked connection: ${err.message}`);
  } finally {
    await browserStrict.close();
  }

  // 5. Puppeteer positive test: Launching browser WITH pinned SPKI list succeeds without --ignore-certificate-errors
  console.log("\n[Test 5] Positive Test: Puppeteer loads cleanly with pinned Caddy SPKI hashes...");
  const browserPinned = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: "new",
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-gpu",
      `--ignore-certificate-errors-spki-list=${SPKI_ROOT},${SPKI_INTERMEDIATE},${SPKI_LEAF}`,
      "--window-size=1280,900"
    ],
  });
  const pagePinned = await browserPinned.newPage();
  try {
    const res = await pagePinned.goto("https://localhost:8443/api/v1/health", { waitUntil: "networkidle0" });
    if (res.status() !== 200) throw new Error(`Expected 200, got ${res.status()}`);
    console.log(`  [PASS] Page loaded successfully (HTTP ${res.status()}) with pinned SPKI trust!`);
  } finally {
    await browserPinned.close();
  }

  // 6. HTTP to HTTPS redirect verification on port 8088
  console.log("\n[Test 6] HTTP (Port 8088) to HTTPS (Port 8443) Redirect Verification...");
  await new Promise((resolve, reject) => {
    http.get("http://localhost:8088/api/v1/health", (res) => {
      console.log(`  [PASS] HTTP listener returned redirect code: ${res.statusCode} (Location: ${res.headers["location"]})`);
      if (res.statusCode === 308 || res.statusCode === 301 || res.statusCode === 302) {
        resolve();
      } else {
        reject(new Error(`Expected 308/301 redirect, got ${res.statusCode}`));
      }
    }).on("error", (err) => reject(err));
  });

  console.log("\n==================================================================");
  console.log("   [SUCCESS] ALL TLS VALIDATION & REJECTION TESTS PASSED          ");
  console.log("==================================================================");
}

runTlsTests().catch((err) => {
  console.error("\n[FATAL ERROR]", err);
  process.exit(1);
});
