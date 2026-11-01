import crypto from "crypto";

function getCsrfSecret(): string {
  const secret =
    process.env.ADMIN_CSRF_SECRET ||
    process.env.CSRF_SECRET_KEY ||
    process.env.JWT_SECRET_KEY;
  if (!secret) {
    if (process.env.NODE_ENV === "production") {
      throw new Error(
        "CRITICAL SECURITY CONFIGURATION ERROR: Anti-CSRF secret key is not configured in production. Set CSRF_SECRET_KEY or ADMIN_CSRF_SECRET."
      );
    }
    return "mediaforge_default_admin_csrf_secret_key_v1";
  }
  return secret;
}

// Configurable trusted origins and hosts for staging and production
const DEFAULT_ALLOWED_HOSTS = new Set([
  "localhost:3000",
  "127.0.0.1:3000",
  "[::1]:3000",
  "localhost",
  "127.0.0.1",
]);

function getTrustedHosts(): Set<string> {
  const hosts = new Set(DEFAULT_ALLOWED_HOSTS);
  const envHosts = process.env.TRUSTED_HOSTS || process.env.ALLOWED_ORIGINS;
  if (envHosts) {
    for (const h of envHosts.split(",")) {
      const trimmed = h.trim();
      if (!trimmed) continue;
      try {
        if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
          hosts.add(new URL(trimmed).host.toLowerCase());
        } else {
          hosts.add(trimmed.toLowerCase());
        }
      } catch {
        hosts.add(trimmed.toLowerCase());
      }
    }
  }
  return hosts;
}

/**
 * Generate a cryptographically secure, session-bound signed anti-CSRF token.
 * Payload includes a 128-bit random nonce, user ID, session ID, and issuance timestamp.
 */
export function createSessionCsrfToken(userId: string, sessionId?: string): string {
  const nonce = crypto.randomBytes(16).toString("hex");
  const timestamp = Date.now().toString();
  const sid = sessionId || "";
  const payload = `${nonce}:${userId}:${sid}:${timestamp}`;
  const hmac = crypto.createHmac("sha256", getCsrfSecret()).update(payload).digest("hex");
  return `${Buffer.from(payload).toString("base64url")}.${hmac}`;
}

/**
 * Validate a session-bound signed anti-CSRF token in constant time.
 * Verifies HMAC integrity, validity window (24 hours), user ID matching, and session ID matching.
 */
export function verifySessionCsrfToken(
  token: string,
  expectedUserId?: string,
  expectedSessionId?: string
): boolean {
  if (!token || typeof token !== "string" || !token.includes(".")) {
    return false;
  }

  const [b64Payload, signature] = token.split(".");
  if (!b64Payload || !signature) {
    return false;
  }

  try {
    const payload = Buffer.from(b64Payload, "base64url").toString("utf-8");
    const expectedHmac = crypto.createHmac("sha256", getCsrfSecret()).update(payload).digest("hex");

    if (signature.length !== expectedHmac.length) {
      return false;
    }

    const sigBuf = Buffer.from(signature, "hex");
    const expBuf = Buffer.from(expectedHmac, "hex");
    if (sigBuf.length !== expBuf.length || !crypto.timingSafeEqual(sigBuf, expBuf)) {
      return false;
    }

    const parts = payload.split(":");
    if (parts.length < 3) {
      return false;
    }

    let nonce: string;
    let userId: string;
    let sid: string = "";
    let timestampStr: string;

    if (parts.length >= 4) {
      [nonce, userId, sid, timestampStr] = parts;
    } else {
      [nonce, userId, timestampStr] = parts;
    }

    if (!nonce || !userId || !timestampStr) {
      return false;
    }

    const timestamp = parseInt(timestampStr, 10);
    if (isNaN(timestamp) || Date.now() - timestamp > 86400000 || timestamp > Date.now() + 60000) {
      return false;
    }

    if (expectedUserId && userId !== expectedUserId) {
      return false;
    }

    // Strict session binding: if an expected session ID is provided and the token contains a session ID,
    // they must match.
    if (expectedSessionId && sid && sid !== expectedSessionId) {
      return false;
    }

    return true;
  } catch {
    return false;
  }
}

/**
 * Validate that state-mutating requests originated from a trusted canonical host and origin.
 * Enforces:
 * 1. The Host header must be among trusted canonical hosts.
 * 2. The Origin / Referer header must match the request host and be trusted.
 * 3. In production, unverified cross-origin requests are rejected immediately.
 */
export function validateRequestOrigin(req: Request): boolean {
  const host = (req.headers.get("x-forwarded-host") || req.headers.get("host") || "").toLowerCase().trim();
  const trustedHosts = getTrustedHosts();

  // If host is present, verify host header matches trusted configuration
  if (host && !trustedHosts.has(host) && !host.startsWith("localhost:") && !host.startsWith("127.0.0.1:")) {
    return false;
  }

  const origin = req.headers.get("origin");
  if (origin) {
    try {
      const originUrl = new URL(origin);
      const originHost = originUrl.host.toLowerCase();
      // Must match request host AND be in trusted hosts
      if (host && originHost !== host) {
        return false;
      }
      return trustedHosts.has(originHost) || originHost.startsWith("localhost:") || originHost.startsWith("127.0.0.1:");
    } catch {
      return false;
    }
  }

  const referer = req.headers.get("referer");
  if (referer) {
    try {
      const refUrl = new URL(referer);
      const refHost = refUrl.host.toLowerCase();
      if (host && refHost !== host) {
        return false;
      }
      return trustedHosts.has(refHost) || refHost.startsWith("localhost:") || refHost.startsWith("127.0.0.1:");
    } catch {
      return false;
    }
  }

  // Reject mutating requests in production if neither Origin nor Referer is provided
  if (process.env.NODE_ENV === "production") {
    const method = req.method.toUpperCase();
    if (["POST", "PUT", "DELETE", "PATCH"].includes(method)) {
      return false;
    }
  }

  return true;
}
