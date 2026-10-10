import { NextResponse } from "next/server";
import { SESSION_COOKIE, backendBaseUrl, isSameOrigin, sessionCookieOptions } from "@/lib/session";

export const dynamic = "force-dynamic";

/** Exchange email + password for the backend JWT and store it in an httpOnly cookie. */
export async function POST(request: Request) {
  if (!isSameOrigin(request)) {
    return NextResponse.json({ detail: "Cross-origin request refused." }, { status: 403 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request." }, { status: 400 });
  }
  const { email, password } = (body ?? {}) as { email?: unknown; password?: unknown };
  if (typeof email !== "string" || typeof password !== "string") {
    return NextResponse.json({ detail: "Email and password are required." }, { status: 422 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${backendBaseUrl()}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "Backend unavailable." }, { status: 502 });
  }

  const data = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    const headers: Record<string, string> = {};
    const retry = upstream.headers.get("retry-after");
    if (retry) headers["Retry-After"] = retry;
    return NextResponse.json(
      { detail: typeof data?.detail === "string" ? data.detail : "Sign-in failed." },
      { status: upstream.status, headers },
    );
  }

  // The token is deliberately NOT returned to the browser.
  const res = NextResponse.json({ subject: data.subject, roles: data.roles });
  res.cookies.set(SESSION_COOKIE, data.access_token, sessionCookieOptions(data.expires_in));
  return res;
}
