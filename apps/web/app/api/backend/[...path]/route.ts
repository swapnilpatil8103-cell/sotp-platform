import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { SESSION_COOKIE, backendBaseUrl, isSameOrigin, sessionCookieOptions } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * Authenticated proxy to the FastAPI backend. The browser calls
 * `/api/backend/<backend path>`; this handler reads the httpOnly session cookie
 * and forwards the request with `Authorization: Bearer <token>`, so the token
 * never reaches page JavaScript.
 *
 * Login is NOT proxied (see /api/auth/login, which sets the cookie), so a
 * browser cannot use this route to read a token back.
 */
async function forward(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (path.some((seg) => seg === ".." || seg === "." || seg === "")) {
    return NextResponse.json({ detail: "Bad path." }, { status: 400 });
  }
  if (path[0] === "auth") {
    return NextResponse.json({ detail: "Not found." }, { status: 404 });
  }

  const isRead = request.method === "GET" || request.method === "HEAD";
  if (!isRead && !isSameOrigin(request)) {
    return NextResponse.json({ detail: "Cross-origin request refused." }, { status: 403 });
  }

  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  }

  const search = new URL(request.url).search;
  const target = `${backendBaseUrl()}/${path.map(encodeURIComponent).join("/")}${search}`;

  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  const contentType = request.headers.get("content-type");
  if (contentType) headers["Content-Type"] = contentType;
  const accept = request.headers.get("accept");
  if (accept) headers["Accept"] = accept;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: isRead ? undefined : await request.arrayBuffer(),
      cache: "no-store",
      redirect: "manual",
    });
  } catch (err) {
    return NextResponse.json(
      { detail: `Backend unavailable: ${err instanceof Error ? err.message : String(err)}` },
      { status: 502 },
    );
  }

  const out = new NextResponse(upstream.status === 204 ? null : upstream.body, { status: upstream.status });
  const upstreamType = upstream.headers.get("content-type");
  if (upstreamType) out.headers.set("Content-Type", upstreamType);
  if (upstream.status === 401) {
    // Expired or revoked: drop the dead cookie so the app sends the user to /login.
    out.cookies.set(SESSION_COOKIE, "", { ...sessionCookieOptions(0), maxAge: 0 });
  }
  return out;
}

export {
  forward as GET,
  forward as POST,
  forward as PUT,
  forward as PATCH,
  forward as DELETE,
};
