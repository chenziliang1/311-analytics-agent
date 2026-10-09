"use client"

import { useEffect, useId, useRef, useState } from "react"
import type { ReactNode } from "react"

/* Placement only. Spelled out in full, rather than built from the side's
 * name, because Tailwind finds class names by reading the source as text. */
const BUBBLE_SIDE = {
  top: "bottom-full top-auto mb-3 mt-0",
  bottom: "top-full bottom-auto mt-3 mb-0",
} as const
const BUBBLE_SIDE_WIDE = {
  top: "lg:bottom-full lg:top-auto lg:mb-3 lg:mt-0",
  bottom: "lg:top-full lg:bottom-auto lg:mt-3 lg:mb-0",
} as const
const CARET_SIDE = {
  top: "top-full bottom-auto -translate-y-1",
  bottom: "bottom-full top-auto translate-y-1",
} as const
const CARET_SIDE_WIDE = {
  top: "lg:top-full lg:bottom-auto lg:-translate-y-1",
  bottom: "lg:bottom-full lg:top-auto lg:translate-y-1",
} as const

/**
 * Explains a domain term on hover, focus, or tap.
 *
 * These four labels name concepts, and a concept name is only readable by
 * someone who already knows the concept. "Widget Yield Variance" is precise
 * to anyone on a widget line and opaque to everyone else, which is the wrong way
 * round for a landing page whose whole job is to bring the reader in.
 *
 * Built rather than installed: the app has no tooltip primitive, and one
 * non-interactive hint does not justify a positioning library.
 *
 * Three ways in, on purpose. Hover is the obvious one and the one nobody on a
 * phone or a keyboard can use, so focus and tap open it too, and Escape closes
 * it. The trigger takes `tabIndex={0}` and points at the bubble with
 * `aria-describedby`, which is the ARIA tooltip pattern: the description is
 * announced with the thing it describes rather than stranded somewhere after
 * it in the reading order.
 */
export default function ConceptTooltip({
  text,
  children,
  align = "start",
  side = "top",
  wideSide,
  className = "",
}: {
  text: string
  children: ReactNode
  /**
   * Which edge the bubble hangs from while the grid is two columns wide.
   *
   * Centring on the card is right on a wide screen and wrong on a phone: the
   * cards are narrower than the bubble there, so a centred bubble on an outer
   * column runs off the screen. Anchoring it to the card's outer edge keeps it
   * on screen, and above `md` -- where the grid opens to four columns and
   * there is room -- both values fall back to centred.
   */
  align?: "start" | "end"
  /**
   * Which side of the trigger the bubble opens on. A trigger near the top of
   * the page, or of a scrolling pane, has no room above it: the bubble would
   * slide under the fixed nav bar or be clipped. `wideSide`, when given,
   * overrides it from `lg` up, for a layout that changes shape there.
   */
  side?: "top" | "bottom"
  wideSide?: "top" | "bottom"
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const id = useId()
  // A tap fires emulated mouseenter and focus before its click, so with all
  // three wired the first two opened the bubble and the click toggled it
  // straight shut again: it took a second tap to see anything. Remembering
  // that the gesture is touch lets the click alone decide.
  const touching = useRef(false)

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false)
    }
    document.addEventListener("keydown", onKeyDown)
    return () => document.removeEventListener("keydown", onKeyDown)
  }, [open])

  return (
    <div
      className={`relative ${className}`}
      tabIndex={0}
      aria-describedby={open ? id : undefined}
      onPointerDown={(event) => {
        touching.current = event.pointerType === "touch"
      }}
      onMouseEnter={() => {
        if (!touching.current) setOpen(true)
      }}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => {
        if (!touching.current) setOpen(true)
      }}
      onBlur={() => setOpen(false)}
      // Touch has no hover state, so a tap has to be able to open it.
      onClick={() => {
        if (touching.current) setOpen((wasOpen) => !wasOpen)
      }}
    >
      {children}

      {open && (
        <div
          id={id}
          role="tooltip"
          // The bubble must never sit between the pointer and the card, or
          // leaving the card through the bubble would flicker it shut.
          className={`pointer-events-none absolute z-30 w-[min(16rem,calc(100vw-2rem))] rounded-xl bg-primary p-4 text-left shadow-xl ring-1 ring-white/10 md:left-1/2 md:right-auto md:-translate-x-1/2 ${
            align === "end" ? "right-0" : "left-0"
          } ${BUBBLE_SIDE[side]} ${wideSide ? BUBBLE_SIDE_WIDE[wideSide] : ""}`}
        >
          <p className="text-xs leading-relaxed text-white/90">{text}</p>
          {/* Caret, a rotated square tucked under the bubble's bottom edge. It
              tracks the bubble's anchor so it keeps pointing at the card. */}
          <span
            aria-hidden="true"
            className={`absolute h-2 w-2 rotate-45 bg-primary md:left-1/2 md:right-auto md:-translate-x-1/2 ${
              align === "end" ? "right-6" : "left-6"
            } ${CARET_SIDE[side]} ${wideSide ? CARET_SIDE_WIDE[wideSide] : ""}`}
          />
        </div>
      )}
    </div>
  )
}
