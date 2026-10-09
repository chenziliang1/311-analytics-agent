"use client"

import { useEffect, useState } from "react"

import CapabilityCard from "@/components/CapabilityCard"
import type { Capability } from "@/lib/site/schema"
import { cn } from "@/lib/utils"

/**
 * The four business lines strung along one road, in the order a request meets
 * them.
 *
 * This is why they are not a card grid: the four threads are not parallel
 * topics but checkpoints on one request's path. It is sorted (routing
 * accuracy), triaged (hidden urgency), closed (true on-time rate) and followed
 * up (escalation), in that order, so the order the cards are authored in
 * `[[capabilities]]` is drawn as a direction of travel. A dataset whose four
 * threads are not a sequence should change the shape here, not the order there.
 *
 * The road starts at a terminal and ends in an arrowhead, and each stop's
 * marker turns warning-sign yellow while that stop is hovered or focused.
 *
 * In a column, the first half of the stops open their explanation downward and
 * the rest upward, so each bubble spreads over the route rather than off its
 * end: the first stop sits too close to the nav bar, or to the top of the chat
 * pane, to open upward. In a row there is room above every stop.
 *
 *   vertical    always a column: the home page's right-hand panel
 *   responsive  a column on narrow screens, a row from `lg` up: the agent's
 *               welcome card. `lg` rather than `md` because ConceptTooltip
 *               centres its bubble from `md`, and four columns at `md` leave
 *               the outer bubbles no room to centre in
 */
export default function CapabilityRoute({
  capabilities,
  orientation = "vertical",
  compact = false,
}: {
  capabilities: Capability[]
  orientation?: "vertical" | "responsive"
  compact?: boolean
}) {
  // The road draws itself in on mount, then the stops arrive along it. Both
  // transitions are disabled under prefers-reduced-motion.
  const [drawn, setDrawn] = useState(false)
  useEffect(() => {
    setDrawn(true)
  }, [])

  const row = orientation === "responsive"

  return (
    <ol className={cn("relative py-6", row && "lg:grid lg:grid-cols-4 lg:gap-3 lg:py-0 lg:pr-4")}>
      {/* The road. */}
      <span
        aria-hidden="true"
        className={cn(
          "absolute left-[14px] top-2 bottom-2 w-1 rounded-full bg-brand",
          "origin-top transition-transform duration-1000 ease-out motion-reduce:transition-none",
          drawn ? "scale-y-100" : "scale-y-0",
          row &&
            "lg:left-2 lg:right-2 lg:top-[7px] lg:bottom-auto lg:h-1 lg:w-auto lg:origin-left lg:scale-y-100",
          row && (drawn ? "lg:scale-x-100" : "lg:scale-x-0"),
        )}
      />
      {/* Terminal: where a resident's report enters. */}
      <span
        aria-hidden="true"
        className={cn(
          "absolute left-[8px] top-0 h-4 w-4 rounded-full border-4 border-brand bg-surface",
          row && "lg:left-0 lg:top-[1px]",
        )}
      />
      {/* Arrowhead: the road continues past the last stop. */}
      <span
        aria-hidden="true"
        className={cn(
          "absolute left-[8px] bottom-0 h-0 w-0 border-x-8 border-t-[11px] border-x-transparent border-t-brand",
          row && "lg:left-auto lg:right-0 lg:bottom-auto lg:top-[4px] lg:-rotate-90",
        )}
      />

      {capabilities.map((capability, index) => (
        <li
          key={capability.title}
          className={cn(
            // Each stop's entrance transform makes it its own stacking
            // context, so a bubble opening downward would sit under the next
            // stop. The active stop is lifted above its neighbours.
            "group/stop relative pl-10 hover:z-20 focus-within:z-20",
            index > 0 && (compact ? "mt-2" : "mt-4"),
            row && "lg:mt-0 lg:pl-0 lg:pt-7",
            "transition-all duration-500 ease-out motion-reduce:transition-none",
            drawn ? "opacity-100 translate-y-0" : "opacity-0 translate-y-2",
          )}
          style={{ transitionDelay: `${300 + index * 180}ms` }}
        >
          {/* The stop's marker, sitting on the road. */}
          <span
            aria-hidden="true"
            className={cn(
              "absolute left-[9px] z-10 h-3.5 w-3.5 rotate-45 rounded-[3px] border-[3px] border-brand bg-surface transition-colors",
              "group-hover/stop:border-primary group-hover/stop:bg-accent",
              "group-focus-within/stop:border-primary group-focus-within/stop:bg-accent",
              compact ? "top-[17px]" : "top-[21px]",
              row && "lg:left-4 lg:top-[2px]",
            )}
          />
          <CapabilityCard
            capability={capability}
            side={index < capabilities.length / 2 ? "bottom" : "top"}
            wideSide={row ? "top" : undefined}
            compact={compact}
            className="h-full"
          />
        </li>
      ))}
    </ol>
  )
}
