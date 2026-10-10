import { NextResponse, type NextRequest } from "next/server";

/**
 * Page guard (Next 16 `proxy`, formerly `middleware`): send visitors without a
 * session cookie to /login. This only checks that the cookie EXISTS so people
 * don't see a half-rendered dashboard of errors; it is not the security
 * boundary. The backend verifies the token on every API call.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const signedIn = request.cookies.has("sotp_session");

  if (pathname === "/login") {
    // Already signed in: skip the form.
    return signedIn ? NextResponse.redirect(new URL("/", request.url)) : NextResponse.next();
  }
  if (!signedIn) {
    const login = new URL("/login", request.url);
    if (pathname !== "/") login.searchParams.set("next", pathname + search);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  // Everything except API routes (which answer 401 themselves) and Next internals/assets.
  matcher: ["/((?!api/|_next/|favicon.ico).*)"],
};
