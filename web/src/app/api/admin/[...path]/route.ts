import { NextRequest, NextResponse } from "next/server";
import { validateRequestOrigin, verifySessionCsrfToken } from "@/lib/csrf";

const API_BASE =
  process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:58000/api/v1";

const MAX_BODY_SIZE_BYTES = 1024 * 1024; // 1 MB limit for administrative JSON payloads

// Hop-by-hop headers that should not be forwarded by reverse proxies
const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
  "host",
]);

async function handleProxy(
  req: NextRequest,
  props: { params: Promise<{ path: string[] }> }
) {
  const { path } = await props.params;

  // 1. Authenticate via HttpOnly session cookie
  const accessToken = req.cookies.get("mf_admin_access_token")?.value;
  if (!accessToken) {
    return NextResponse.json(
      { detail: "Authentication required: no active administrator session" },
      { status: 401 }
    );
  }

  const method = req.method.toUpperCase();

  // 2. Strict Origin and CSRF validation for mutating requests
  if (["POST", "PUT", "DELETE", "PATCH"].includes(method)) {
    // Check Origin / Referer
    if (!validateRequestOrigin(req)) {
      return NextResponse.json(
        { detail: "Cross-site request forgery protection: untrusted origin" },
        { status: 403 }
      );
    }

    const csrfHeader =
      req.headers.get("x-admin-csrf-token") || req.headers.get("x-csrf-token");
    const csrfCookie = req.cookies.get("mf_admin_csrf")?.value;

    if (!csrfHeader || !csrfCookie || csrfHeader !== csrfCookie) {
      return NextResponse.json(
        { detail: "CSRF verification failed: invalid or missing anti-forgery token" },
        { status: 403 }
      );
    }

    // Extract expected user ID and session ID from verified access token payload
    let expectedUserId: string | undefined;
    let expectedSessionId: string | undefined;
    try {
      const parts = accessToken.split(".");
      if (parts.length === 3) {
        const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf-8"));
        expectedUserId = payload.sub;
        expectedSessionId = payload.sid;
      }
    } catch {
      // Ignore token parse error, signature will fail or be caught upstream
    }

    // Verify token cryptographic signature, expiry, and session binding
    if (!verifySessionCsrfToken(csrfHeader, expectedUserId, expectedSessionId)) {
      return NextResponse.json(
        { detail: "CSRF verification failed: invalid, expired, or session-mismatched token signature" },
        { status: 403 }
      );
    }
  }

  // 3. Path sanitization and path traversal prevention
  if (!path || !Array.isArray(path) || path.length === 0) {
    return NextResponse.json({ detail: "Invalid administrative endpoint" }, { status: 400 });
  }

  for (const segment of path) {
    if (
      !segment ||
      segment.includes("..") ||
      segment.includes("/") ||
      segment.includes("\\") ||
      !/^[a-zA-Z0-9_\-.:]+$/.test(segment)
    ) {
      return NextResponse.json(
        { detail: "Invalid path segment: path traversal not permitted" },
        { status: 400 }
      );
    }
  }

  const subpath = path.join("/");
  const search = req.nextUrl.search;
  const targetUrl = `${API_BASE}/admin/${subpath}${search}`;

  // 4. Construct safe upstream headers (strip hop-by-hop & overwrite Authorization)
  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
  };

  const contentType = req.headers.get("content-type");
  if (contentType) {
    headers["Content-Type"] = contentType;
  }

  // 5. Bounded request body read
  let body: BodyInit | null = null;
  if (!["GET", "HEAD"].includes(method)) {
    try {
      const rawText = await req.text();
      if (rawText) {
        if (rawText.length > MAX_BODY_SIZE_BYTES) {
          return NextResponse.json(
            { detail: "Payload too large: administrative request exceeds 1MB limit" },
            { status: 413 }
          );
        }
        body = rawText;
      }
    } catch {
      // No body
    }
  }

  // 6. Proxy upstream to FastAPI
  try {
    const upstreamRes = await fetch(targetUrl, {
      method,
      headers,
      body,
    });

    const data = await upstreamRes.text();
    const responseHeaders = new Headers();
    const upstreamContentType = upstreamRes.headers.get("content-type");
    if (upstreamContentType) {
      responseHeaders.set("Content-Type", upstreamContentType);
    }

    // Cache control to prevent caching of administrative responses in private or shared caches
    responseHeaders.set("Cache-Control", "private, no-store, no-cache, max-age=0, must-revalidate");
    responseHeaders.set("Pragma", "no-cache");
    responseHeaders.set("Expires", "0");
    responseHeaders.set("Surrogate-Control", "no-store");

    return new NextResponse(data, {
      status: upstreamRes.status,
      headers: responseHeaders,
    });
  } catch (err: any) {
    return NextResponse.json(
      { detail: `Upstream error contacting administrative backend: ${err.message}` },
      { status: 502 }
    );
  }
}

export const dynamic = "force-dynamic";
export const revalidate = 0;

export const GET = handleProxy;
export const POST = handleProxy;
export const PUT = handleProxy;
export const DELETE = handleProxy;
export const PATCH = handleProxy;
