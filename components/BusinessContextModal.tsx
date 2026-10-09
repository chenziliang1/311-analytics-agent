"use client"

import { useEffect, useRef } from "react"
import { X } from "lucide-react"

import { Markdown } from "@/components/chat/markdown"

/**
 * The long-form business explainer, as a dismissable overlay.
 *
 * The home page can carry a headline and one paragraph. It cannot carry a whole
 * business domain, and a reader who does not already know the industry has no
 * way to tell what the agent's answers are *for*. This holds the two or three
 * screens that close that gap, without spending a route on it or pushing the
 * call to action below the fold.
 *
 * Dismissal is deliberately generous: the close button, the backdrop, and
 * Escape all work. Clicking *inside* the panel does not close it — that reads
 * as an obvious idea until you try to scroll a long document and it vanishes
 * under you.
 *
 * The body arrives as raw Markdown from `data/business-context.md` and the
 * labels from `[home.businessContext]` in `data/site.toml`, so swapping the
 * dataset rewrites this without touching the component. Rendering goes through
 * the same Markdown component the chat uses, which is why a ```mermaid fence in
 * that document draws a diagram here too, lazily and at securityLevel
 * "strict", without this component knowing anything about diagrams.
 */
export default function BusinessContextModal({
  open,
  onClose,
  labels,
  markdown,
}: {
  open: boolean
  onClose: () => void
  labels: { title: string; subtitle: string }
  markdown: string
}) {
  const panelRef = useRef<HTMLDivElement>(null)

  // Escape closes, and the page behind must not scroll while the overlay is up.
  useEffect(() => {
    if (!open) return

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose()
    }
    document.addEventListener("keydown", onKeyDown)

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"

    // Move focus into the panel so Escape and the scroll keys land here rather
    // than on whatever was focused on the page underneath.
    panelRef.current?.focus()

    return () => {
      document.removeEventListener("keydown", onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 bg-primary/70 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="business-context-title"
        tabIndex={-1}
        // The backdrop closes on click, so a click that starts inside the panel
        // must not bubble up to it.
        onClick={(event) => event.stopPropagation()}
        className="relative w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden rounded-2xl bg-surface border border-border shadow-2xl outline-none"
      >
        {/* Stays put while the document scrolls, so it is white enough to read
            on the green header and the white body alike. */}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute top-5 right-5 z-10 flex items-center justify-center w-9 h-9 rounded-full bg-white text-brand shadow-sm hover:bg-accent hover:text-primary transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="overflow-y-auto">
          {/* The header is a guide sign, the same face as the home page's
              headline, so the explainer reads as part of the same street. */}
          <div className="bg-brand px-6 sm:px-10 pt-8 pb-7 sm:pt-10 sm:pb-8 text-white">
            <h2
              id="business-context-title"
              className="font-display text-3xl sm:text-4xl font-extrabold mb-3 pr-12"
            >
              {labels.title}
            </h2>
            <p className="text-base sm:text-lg text-white/80 leading-relaxed">
              {labels.subtitle}
            </p>
          </div>
          <div className="h-1.5 bg-accent" aria-hidden="true" />

          <div className="px-6 sm:px-10 pb-8 sm:pb-10">
            {/* The shared Markdown component is sized for chat bubbles. This is a
                document, so the headings and body are scaled up here rather than
                by forking the renderer. */}
            <div
              className="mt-8 text-base text-primary/80
                         [&_h2]:font-display [&_h2]:text-xl [&_h2]:sm:text-2xl [&_h2]:font-extrabold
                         [&_h2]:text-brand [&_h2]:mt-10 [&_h2]:mb-3
                         [&_h3]:text-lg [&_h3]:text-primary [&_h3]:mt-6
                         [&_li]:mb-2 [&_strong]:text-primary"
            >
              <Markdown>{markdown}</Markdown>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
