import { NextRequest, NextResponse } from "next/server";
import { createSessionCsrfToken, verifySessionCsrfToken } from "@/lib/csrf";

const API_BASE = process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:58000/api/v1";

export async function GET(req: NextRequest) {
  let accessToken = req.cookies.get("mf_admin_access_token")?.value;
  const refreshToken = req.cookies.get("mf_admin_refresh_token")?.value;

  const isSecure = process.env.NODE_ENV === "production";

  // If access token missing but refresh token exists, attempt rotation
  if (!accessToken && refreshToken) {
    try {
      const refreshRes = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (refreshRes.ok) {
        const refreshData = await refreshRes.json();
        accessToken = refreshData.access_token;
      }
    } catch {
      // Refresh failed
    }
  }

  if (!accessToken) {
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }

  try {
    const meRes = await fetch(`${API_BASE}/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    if (!meRes.ok) {
      // Token invalid or revoked
      const response = NextResponse.json({ authenticated: false }, { status: 401 });
      response.cookies.delete("mf_admin_access_token");
      response.cookies.delete("mf_admin_csrf");
      return response;
    }

    const userProfile = await meRes.json();
    const isSystemAdmin =
      userProfile.role === "system_admin" ||
      userProfile.role === "system" ||
      (userProfile.role === "owner" && userProfile.tenant_id === "00000000-0000-0000-0000-000000000001");

    if (!isSystemAdmin) {
      const response = NextResponse.json(
        { authenticated: false, detail: "Insufficient administrative privileges" },
        { status: 403 }
      );
      response.cookies.delete("mf_admin_access_token");
      response.cookies.delete("mf_admin_csrf");
      return response;
    }

    const response = NextResponse.json({
      authenticated: true,
      user: {
        id: userProfile.user_id,
        email: userProfile.email,
        displayName: userProfile.display_name,
        role: userProfile.role,
        tenantId: userProfile.tenant_id,
        tier: userProfile.tier,
      },
    });

    // Refresh access token cookie if it was freshly rotated
    if (req.cookies.get("mf_admin_access_token")?.value !== accessToken) {
      response.cookies.set("mf_admin_access_token", accessToken, {
        httpOnly: true,
        secure: isSecure,
        sameSite: "lax",
        path: "/",
        maxAge: 1800,
      });
    }

    // Extract session ID from access token payload
    let sessionId: string | undefined;
    try {
      const parts = accessToken.split(".");
      if (parts.length === 3) {
        const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf-8"));
        sessionId = payload.sid;
      }
    } catch {}

    // Ensure valid, session-bound CSRF cookie exists
    const existingCsrf = req.cookies.get("mf_admin_csrf")?.value;
    if (!existingCsrf || !verifySessionCsrfToken(existingCsrf, userProfile.user_id, sessionId)) {
      response.cookies.set("mf_admin_csrf", createSessionCsrfToken(userProfile.user_id, sessionId), {
        httpOnly: false,
        secure: isSecure,
        sameSite: "lax",
        path: "/",
        maxAge: 1800,
      });
    }

    return response;
  } catch (err: any) {
    return NextResponse.json({ authenticated: false, detail: err.message }, { status: 500 });
  }
}
