import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

/**
 * A guide-sign panel: a green face with a white border inset from its edge,
 * which is how a real road sign is built.
 *
 * This is the app's one visual signature, so it is one component rather than a
 * class string repeated at each size. It appears as the headline on the home
 * page, as the agent's name on the welcome card, as the agent's avatar, and as
 * the small plate behind each capability icon. At every size the proportion
 * is the same: a thin green margin, then the white rule, then the face.
 */
const FRAME = {
  sm: { outer: "rounded-md p-[2px]", inner: "rounded-[4px] border" },
  md: { outer: "rounded-xl p-1", inner: "rounded-lg border-2" },
  lg: { outer: "rounded-2xl p-1.5 sm:p-2", inner: "rounded-xl border-[3px]" },
} as const

export function SignPlate({
  size = "md",
  className,
  innerClassName,
  children,
}: {
  size?: keyof typeof FRAME
  className?: string
  innerClassName?: string
  children: ReactNode
}) {
  const frame = FRAME[size]
  return (
    <div className={cn("bg-brand text-brand-foreground", frame.outer, className)}>
      <div className={cn("h-full w-full border-white", frame.inner, innerClassName)}>
        {children}
      </div>
    </div>
  )
}
