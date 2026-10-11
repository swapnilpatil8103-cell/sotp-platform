/**
 * Server-only helpers for the login session.
 *
 * The backend's JWT lives in an httpOnly cookie that browser JavaScript cannot
 * read; only the Next.js route handlers under `app/api` ever see it, and they
 * attach it to backend calls. Nothing here may be imported by a client
 * component.
 */

import { createHash, timingSafeEqual } from "node:crypto";
import { isIP } from "node:net";

export const SESSION_COOKIE = "sotp_session";

/** Header our reverse proxy (Caddy) sets to a shared secret on every request it forwards. */
export const PROXY_SECRET_HEADER = "x-proxy-secret";

/**
 * True only if the request carries the shared secret the reverse proxy attaches
 * (PROXY_SHARED_SECRET). Without that configured, nothing is considered to have come
 * through the proxy. The compare is constant-time. Forwarding headers (X-Forwarded-For,
 * X-Forwarded-Host) are believed only for such requests: a client that reaches this
 * server another way cannot pick its own address or host.
 */
export function cameThroughProxy(request: Request): boolean {
  const secret = process.env.PROXY_SHARED_SECRET;
  const presented = request.headers.get(PROXY_SECRET_HEADER);
  if (!secret || !presented) return false;
  const digest = (v: string) => createHash("sha256").update(v).digest();
  return timingSafeEqual(digest(secret), digest(presented));
}

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
  const forwardedHost = cameThroughProxy(request) ? request.headers.get("x-forwarded-host") : null;
  const host = forwardedHost ?? request.headers.get("host");
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

/** Largest request body the proxy will forward. Mirrors the backend's MAX_REQUEST_BODY_BYTES (default 1 MiB). */
export function maxBodyBytes(): number {
  const n = Number(process.env.MAX_PROXY_BODY_BYTES);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 1024 * 1024;
}

/** Credential routes only ever receive a few short strings. */
export const AUTH_BODY_BYTES = 16 * 1024;

/**
 * Read a request body, giving up (null) as soon as it exceeds `limit` bytes.
 * Checks Content-Length first, then counts the bytes actually streamed, so a
 * missing or lying Content-Length cannot be used to buffer an unbounded body.
 */
export async function readBodyLimited(request: Request, limit: number): Promise<ArrayBuffer | null> {
  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > limit) return null;
  if (!request.body) return new ArrayBuffer(0);

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.byteLength;
  }
  return out.buffer;
}

/** The standard 413 body, shared by every route. */
export function tooLarge(limit: number) {
  return Response.json({ detail: `Request body too large (limit ${limit} bytes).` }, { status: 413 });
}

/**
 * Client address to hand to the backend's login throttle.
 *
 * Only the address supplied by our reverse proxy is forwarded: when the request carries
 * the proxy's shared secret, the (single) address Caddy wrote into X-Forwarded-For. Any
 * X-Forwarded-For on a request that did NOT come through the proxy is dropped, so the
 * backend sees this server's own address instead of one the client chose. The secret
 * itself is never forwarded.
 */
export function forwardedForHeaders(request: Request): Record<string, string> {
  if (!cameThroughProxy(request)) return {};
  const entries = (request.headers.get("x-forwarded-for") ?? "").split(",");
  const address = entries[entries.length - 1].trim(); // the proxy overwrites the header, so this is its only entry
  return isIP(address) ? { "X-Forwarded-For": address } : {};
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
