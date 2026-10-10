import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { SESSION_COOKIE, readClaims } from "@/lib/session";

export const dynamic = "force-dynamic";

/** Who is signed in (for the header). Claims are read for display only; the backend verifies every call. */
export async function GET() {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  const claims = token ? readClaims(token) : null;
  if (!claims?.sub || (claims.exp && claims.exp * 1000 < Date.now())) {
    return NextResponse.json({ detail: "Not signed in." }, { status: 401 });
  }
  return NextResponse.json({ subject: claims.sub, roles: claims.roles ?? [], expires_at: claims.exp ?? null });
}
