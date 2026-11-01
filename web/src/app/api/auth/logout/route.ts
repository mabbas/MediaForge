import { NextRequest, NextResponse } from "next/server";

const API_BASE = process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:58000/api/v1";

export async function POST(req: NextRequest) {
  const accessToken = req.cookies.get("mf_admin_access_token")?.value;

  if (accessToken) {
    try {
      await fetch(`${API_BASE}/auth/logout`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
      });
    } catch {
      // Ignore upstream logout error
    }
  }

  const response = NextResponse.json({ success: true, message: "Logged out" });
  response.cookies.set("mf_admin_access_token", "", {
    httpOnly: true,
    path: "/",
    maxAge: 0,
    expires: new Date(0),
  });
  response.cookies.set("mf_admin_refresh_token", "", {
    httpOnly: true,
    path: "/api/auth",
    maxAge: 0,
    expires: new Date(0),
  });
  response.cookies.set("mf_admin_csrf", "", {
    path: "/",
    maxAge: 0,
    expires: new Date(0),
  });

  return response;
}
