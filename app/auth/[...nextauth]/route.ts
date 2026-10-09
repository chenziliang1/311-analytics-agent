// Auth.js's sign-in, callback and session endpoints, under /auth (see lib/auth.ts).
import { NextResponse } from "next/server"

import { authEnabled, handlers } from "@/lib/auth"

// With sign-in off there is no secret, and Auth.js answers every call with a
// 500. The chat asks /auth/session on load; "no session" is the true answer.
const off = () => NextResponse.json(null)

export const GET = authEnabled ? handlers.GET : off
export const POST = authEnabled ? handlers.POST : off
