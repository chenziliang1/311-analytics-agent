import NextAuth from "next-auth"
import GitHub from "next-auth/providers/github"

/**
 * Optional sign-in, GitHub as the identity provider.
 *
 * **Off unless configured.** Without `AUTH_GITHUB_ID` there is no sign-in at
 * all, which is what local development wants. On Vercel it needs
 * `AUTH_GITHUB_ID`, `AUTH_GITHUB_SECRET` and `AUTH_SECRET`;
 * `AUTH_ALLOWED_GITHUB_USERS`, a comma-separated list of logins, narrows who
 * may sign in (empty lets any GitHub account in).
 *
 * **Optional by default.** A visitor who does not sign in uses the site as
 * `anonymous`: the warehouse tool runs their queries as whatever role that
 * login maps to (TENANT_ROLE_MAP), and the spend caps in `api/index.py` ration
 * them by network address. `AUTH_REQUIRE_SIGN_IN="true"` restores the gate
 * that sends every visitor to GitHub first.
 *
 * Mounted at /auth rather than Auth.js's default /api/auth, because every
 * /api path is rewritten to the FastAPI backend. The GitHub OAuth app's
 * callback URL is therefore `<site>/auth/callback/github`.
 */
export const authEnabled = Boolean(process.env.AUTH_GITHUB_ID)

/** Whether a visitor without a session is turned away rather than let in as anonymous. */
export const signInRequired = authEnabled && process.env.AUTH_REQUIRE_SIGN_IN === "true"

const allowedLogins = (process.env.AUTH_ALLOWED_GITHUB_USERS ?? "")
  .split(",")
  .map((login) => login.trim().toLowerCase())
  .filter(Boolean)

export const { handlers, auth } = NextAuth({
  basePath: "/auth",
  providers: [GitHub],
  callbacks: {
    signIn: ({ profile }) => {
      if (!allowedLogins.length) return true
      const login = typeof profile?.login === "string" ? profile.login.toLowerCase() : ""
      return allowedLogins.includes(login)
    },
    // Used by proxy.ts: a request without a session is sent to sign in.
    authorized: ({ auth: session }) => Boolean(session?.user),
    // The GitHub login is the stable id; the display name can be empty.
    jwt: ({ token, profile }) => {
      if (profile && typeof profile.login === "string") token.login = profile.login
      return token
    },
    session: ({ session, token }) => {
      if (session.user && typeof token.login === "string") {
        session.user.name = session.user.name || token.login
        ;(session.user as { login?: string }).login = token.login
      }
      return session
    },
  },
})
