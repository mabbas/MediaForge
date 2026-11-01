import { NextRequest, NextResponse } from "next/server";
import { createSessionCsrfToken } from "@/lib/csrf";

const API_BASE = process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:58000/api/v1";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { email, password } = body;

    if (!email || !password) {
      return NextResponse.json({ detail: "Email and password are required" }, { status: 400 });
    }

    // Authenticate with FastAPI
    const loginRes = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, tenant_slug: "default" }),
    });

    if (!loginRes.ok) {
      const err = await loginRes.json().catch(() => ({ detail: "Authentication failed" }));
      return NextResponse.json(err, { status: loginRes.status });
    }

    const tokenData = await loginRes.json();
    const accessToken = tokenData.access_token;
    const refreshToken = tokenData.refresh_token;

    // Verify user role & identity via /auth/me
    const meRes = await fetch(`${API_BASE}/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    if (!meRes.ok) {
      return NextResponse.json({ detail: "Failed to verify principal credentials" }, { status: 401 });
    }

    const userProfile = await meRes.json();

    // Strict system administrator RBAC check
    const isSystemAdmin =
      userProfile.role === "system_admin" ||
      userProfile.role === "system" ||
      (userProfile.role === "owner" && userProfile.tenant_id === "00000000-0000-0000-0000-000000000001");

    if (!isSystemAdmin) {
      return NextResponse.json(
        { detail: "Access denied: Commercial administration portal requires system administrator privileges" },
        { status: 403 }
      );
    }

    // Cryptographically signed, session-bound CSRF token
    const csrfToken = createSessionCsrfToken(userProfile.user_id, tokenData.session_id);

    const response = NextResponse.json({
      success: true,
      user: {
        id: userProfile.user_id,
        email: userProfile.email,
        displayName: userProfile.display_name,
        role: userProfile.role,
        tenantId: userProfile.tenant_id,
        tier: userProfile.tier,
      },
    });

    const isSecure = process.env.NODE_ENV === "production";

    // Set HttpOnly access token cookie
    response.cookies.set("mf_admin_access_token", accessToken, {
      httpOnly: true,
      secure: isSecure,
      sameSite: "lax",
      path: "/",
      maxAge: 1800, // 30 minutes
    });

    // Set HttpOnly refresh token cookie
    if (refreshToken) {
      response.cookies.set("mf_admin_refresh_token", refreshToken, {
        httpOnly: true,
        secure: isSecure,
        sameSite: "lax",
        path: "/api/auth",
        maxAge: 604800, // 7 days
      });
    }

    // Set readable CSRF token cookie
    response.cookies.set("mf_admin_csrf", csrfToken, {
      httpOnly: false,
      secure: isSecure,
      sameSite: "lax",
      path: "/",
      maxAge: 1800,
    });

    return response;
  } catch (error: any) {
    return NextResponse.json({ detail: error.message || "Internal server error" }, { status: 500 });
  }
}
