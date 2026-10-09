"use client"

import ConceptTooltip from "@/components/ConceptTooltip"
import { SignPlate } from "@/components/SignPlate"
import { getIcon } from "@/lib/icons"
import type { Capability } from "@/lib/site/schema"
import { cn } from "@/lib/utils"

/**
 * One of the app's four business lines, as a stop with an explanation behind it.
 *
 * The home page and the agent's welcome screen both show these four, at
 * different sizes, each time strung along a route by `CapabilityRoute`. One
 * component rather than two so they cannot drift: the previous arrangement had
 * `home.capabilities` and `chat.insights` as separate config, and they had
 * already drifted -- the second thread was "Portfolio Concentration" on the
 * home page and "Portfolio Health" on the agent, with a different icon, and the
 * tooltip existed on one screen and not the other.
 *
 * `title` is a concept name and is therefore unreadable to anyone outside the
 * domain. `subtitle` is the one-line translation; the tooltip is the paragraph
 * behind it, on hover, focus, or tap.
 */
export default function CapabilityCard({
  capability,
  align = "start",
  side = "top",
  wideSide,
  compact = false,
  className = "",
}: {
  capability: Capability
  /** Which edge the tooltip hangs from while the grid is two columns wide. */
  align?: "start" | "end"
  /** Which side the tooltip opens on; see ConceptTooltip. */
  side?: "top" | "bottom"
  wideSide?: "top" | "bottom"
  /** The agent's welcome screen has less room than the home page. */
  compact?: boolean
  className?: string
}) {
  const Icon = getIcon(capability.icon)

  return (
    <ConceptTooltip
      text={capability.tooltip}
      align={align}
      side={side}
      wideSide={wideSide}
      className={cn(
        "group flex gap-3 rounded-xl border border-transparent cursor-help transition-colors",
        "hover:border-border hover:bg-background",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-light",
        compact ? "px-3 py-2.5" : "px-4 py-3",
        className,
      )}
    >
      {/* The icon wears the same sign face as the headline: each stop on the
          route has its own small sign. */}
      <SignPlate size="sm" className={cn("shrink-0", compact ? "h-7 w-7" : "h-8 w-8")}>
        <span className="flex h-full w-full items-center justify-center">
          <Icon className={compact ? "h-3.5 w-3.5" : "h-4 w-4"} aria-hidden="true" />
        </span>
      </SignPlate>

      <div className="min-w-0">
        <p
          className={cn(
            "font-display font-bold leading-tight text-primary",
            compact ? "text-sm" : "text-base",
          )}
        >
          {capability.title}
        </p>
        <p
          className={cn(
            "mt-1 leading-snug text-muted-foreground",
            compact ? "text-xs" : "text-sm",
          )}
        >
          {capability.subtitle}
        </p>
        {/* The dashed rule is the only thing telling a reader there is something
            to hover over. Without it the hint is invisible. */}
        <span
          aria-hidden="true"
          className="mt-2.5 block w-8 border-b-2 border-dashed border-brand-soft transition-colors group-hover:border-accent"
        />
      </div>
    </ConceptTooltip>
  )
}
