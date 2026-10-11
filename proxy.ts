import { NextResponse, type NextRequest } from "next/server"

import { auth, authEnabled, signInRequired } from "@/lib/auth"

/** Who the backend takes the request to be from. Only this file may set it. */
const USER_HEADER = "x-lens-user"

/**
 * The request as the backend should see it: `x-lens-user` is the signed-in
 * login, or absent. Whatever the browser sent under that name is dropped
 * first, since the backend trusts the header and anyone can type one.
 */
function identified(request: NextRequest, login: string | null) {
  const headers = new Headers(request.headers)
  headers.delete(USER_HEADER)
  if (login) headers.set(USER_HEADER, login)
  return NextResponse.next({ request: { headers } })
}

/**
 * With sign-in configured (lib/auth.ts): read the session and name the user
 * to the backend. It runs before the /api rewrite, so the FastAPI backend is
 * behind it too. A visitor without a session goes through as anonymous,
 * unless `AUTH_REQUIRE_SIGN_IN` turns the site into a gate again.
 */
const gate = auth((request) => {
  const user = request.auth?.user as { login?: string; name?: string | null } | undefined
  if (!user && signInRequired) {
    if (request.nextUrl.pathname.startsWith("/api/")) {
      return NextResponse.json({ error: "sign in first" }, { status: 401 })
    }
    const signIn = new URL("/auth/signin", request.nextUrl.origin)
    signIn.searchParams.set("callbackUrl", request.nextUrl.href)
    return NextResponse.redirect(signIn)
  }
  return identified(request, user ? (user.login ?? user.name ?? "") : null)
})

/** Without sign-in there is no session to read, but the header is still not the browser's to set. */
const open = (request: NextRequest) => identified(request, null)

export default authEnabled ? gate : open

export const config = {
  // Everything except Auth.js's own routes and static assets.
  matcher: ["/((?!auth|_next/static|_next/image|icon.png|favicon.ico).*)"],
}
