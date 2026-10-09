import { NextResponse } from "next/server"

import { auth, authEnabled } from "@/lib/auth"

/**
 * The sign-in gate: every page and every /api call needs a session, when
 * sign-in is configured (lib/auth.ts). It runs before the /api rewrite, so
 * the FastAPI backend is behind it too, and it passes the signed-in login on
 * as `x-lens-user`, overwriting whatever the browser sent under that name.
 */
const gate = auth((request) => {
  if (!request.auth?.user) {
    if (request.nextUrl.pathname.startsWith("/api/")) {
      return NextResponse.json({ error: "sign in first" }, { status: 401 })
    }
    const signIn = new URL("/auth/signin", request.nextUrl.origin)
    signIn.searchParams.set("callbackUrl", request.nextUrl.href)
    return NextResponse.redirect(signIn)
  }
  const user = request.auth.user as { login?: string; name?: string | null }
  const headers = new Headers(request.headers)
  headers.set("x-lens-user", user.login ?? user.name ?? "")
  return NextResponse.next({ request: { headers } })
})

export default authEnabled ? gate : () => NextResponse.next()

export const config = {
  // Everything except Auth.js's own routes and static assets.
  matcher: ["/((?!auth|_next/static|_next/image|icon.png|favicon.ico).*)"],
}
