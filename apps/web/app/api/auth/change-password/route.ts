import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  AUTH_BODY_BYTES,
  SESSION_COOKIE,
  backendBaseUrl,
  forwardedForHeaders,
  isSameOrigin,
  readBodyLimited,
  sessionCookieOptions,
  tooLarge,
} from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * Change the signed-in user's password. The backend invalidates every existing
 * token (including the one in this request's cookie) and returns a fresh one,
 * which replaces the cookie so this browser stays signed in.
 */
export async function POST(request: Request) {
  if (!isSameOrigin(request)) {
    return NextResponse.json({ detail: "Cross-origin request refused." }, { status: 403 });
  }
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not signed in." }, { status: 401 });

  const raw = await readBodyLimited(request, AUTH_BODY_BYTES);
  if (raw === null) return tooLarge(AUTH_BODY_BYTES);
  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return NextResponse.json({ detail: "Invalid request." }, { status: 400 });
  }
  const { current_password, new_password } = (body ?? {}) as Record<string, unknown>;
  if (typeof current_password !== "string" || typeof new_password !== "string") {
    return NextResponse.json({ detail: "Current and new password are required." }, { status: 422 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${backendBaseUrl()}/auth/change-password`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...forwardedForHeaders(request),
      },
      body: JSON.stringify({ current_password, new_password }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "Backend unavailable." }, { status: 502 });
  }

  const data = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    let detail = "Could not change the password.";
    if (typeof data?.detail === "string") detail = data.detail;
    else if (Array.isArray(data?.detail)) detail = "The new password must be at least 12 characters.";
    const headers: Record<string, string> = {};
    const retry = upstream.headers.get("retry-after");
    if (retry) headers["Retry-After"] = retry;
    const res = NextResponse.json({ detail }, { status: upstream.status, headers });
    if (upstream.status === 401) res.cookies.set(SESSION_COOKIE, "", { ...sessionCookieOptions(0), maxAge: 0 });
    return res;
  }

  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, data.access_token, sessionCookieOptions(data.expires_in));
  return res;
}
