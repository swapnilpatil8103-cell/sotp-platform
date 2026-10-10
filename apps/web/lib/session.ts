/**
 * Server-only helpers for the login session.
 *
 * The backend's JWT lives in an httpOnly cookie that browser JavaScript cannot
 * read; only the Next.js route handlers under `app/api` ever see it, and they
 * attach it to backend calls. Nothing here may be imported by a client
 * component.
 */

export const SESSION_COOKIE = "sotp_session";

/** Where the FastAPI backend is reachable FROM THE NEXT.JS SERVER (not the browser). */
export function backendBaseUrl(): string {
  return (process.env.API_BASE_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

/**
 * Cookie attributes. `Secure` is on in production builds; set
 * SESSION_COOKIE_SECURE=false only for a production build served over plain
 * http (e.g. `docker compose` on localhost).
 */
export function sessionCookieOptions(maxAgeSeconds: number) {
  const secure =
    process.env.SESSION_COOKIE_SECURE !== undefined
      ? process.env.SESSION_COOKIE_SECURE.toLowerCase() !== "false"
      : process.env.NODE_ENV === "production";
  return {
    httpOnly: true,
    secure,
    sameSite: "strict" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}

/** Reject cross-site state-changing requests (defence in depth on top of SameSite=Strict). */
export function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true; // same-origin navigations and non-browser clients omit it
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export type SessionInfo = { subject: string; roles: string[]; expires_at: number };

/** Read the (unverified) claims of our own token, for display only. The backend remains the authority. */
export function readClaims(token: string): { sub?: string; roles?: string[]; exp?: number } | null {
  try {
    const payload = token.split(".")[1];
    return JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
  } catch {
    return null;
  }
}
