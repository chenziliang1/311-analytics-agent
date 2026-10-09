"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { ArrowDown, ArrowRight, BookOpen } from "lucide-react"

import BusinessContextModal from "@/components/BusinessContextModal"
import CapabilityRoute from "@/components/CapabilityRoute"
import { SignPlate } from "@/components/SignPlate"
import type { SiteConfig } from "@/lib/site/schema"

/**
 * The home page, composed as a street corner: a guide sign on the left with
 * its supplemental plaque bolted underneath, and on the right the road it
 * points at, along which the four business lines sit as stops.
 *
 * The sign's arrow is not decoration. It points at the route -- right while
 * the route sits beside it, down once the columns stack on a narrow screen.
 */
export default function Hero({
  site,
  businessContext,
}: {
  site: SiteConfig
  /** Raw Markdown of `data/business-context.md`, read on the server. */
  businessContext: string
}) {
  const [isVisible, setIsVisible] = useState(false)
  const [contextOpen, setContextOpen] = useState(false)

  useEffect(() => {
    setIsVisible(true)
  }, [])

  const { home, capabilities } = site

  // Every entrance below shares this, so reduced motion is one switch.
  const reveal = (shown: string, hidden: string) =>
    `transition-all duration-700 ease-out motion-reduce:transition-none ${isVisible ? shown : hidden}`

  return (
    <section className="px-4 pt-24 pb-12 sm:px-6 sm:pt-28 lg:px-8 lg:pb-16">
      <div className="mx-auto grid w-full max-w-6xl items-start gap-10 lg:grid-cols-12 lg:gap-14">
        {/* The sign */}
        <div className="lg:col-span-7">
          <div className={`flex flex-col items-start ${reveal("opacity-100 translate-y-0", "opacity-0 -translate-y-6")}`}>
            <SignPlate
              size="lg"
              className="max-w-full shadow-[0_24px_48px_-24px_rgba(0,61,45,0.55)]"
            >
              <div className="flex items-end gap-5 px-5 py-5 sm:gap-8 sm:px-9 sm:py-8">
                <h1 className="font-display text-[2.6rem] font-extrabold leading-[0.95] tracking-tight text-white sm:text-6xl lg:text-7xl">
                  <span className="block">{home.headline.prefix.trim()}</span>
                  <span className="block text-accent">{home.headline.accent}</span>
                </h1>
                <ArrowRight
                  className="mb-1 hidden h-14 w-14 shrink-0 text-white lg:block"
                  strokeWidth={2.75}
                  aria-hidden="true"
                />
                <ArrowDown
                  className="mb-1 h-10 w-10 shrink-0 text-white sm:h-14 sm:w-14 lg:hidden"
                  strokeWidth={2.75}
                  aria-hidden="true"
                />
              </div>
            </SignPlate>

            {/* The supplemental plaque: a smaller white sign bolted under the
                main one, saying what the main one is for. */}
            <p className="ml-3 mt-2 rounded-lg border-[3px] border-brand bg-surface px-3.5 py-1.5 font-display text-sm font-bold uppercase tracking-[0.06em] text-brand sm:text-base">
              {home.tagline}
            </p>
          </div>

          <div className={`delay-200 ${reveal("opacity-100 translate-y-0", "opacity-0 translate-y-6")}`}>
            <p className="mt-9 max-w-xl text-lg leading-relaxed text-primary/80 sm:text-xl">
              {home.description}
            </p>

            {/* One paragraph cannot carry a whole business domain; this opens the
                two or three screens that can. */}
            <button
              type="button"
              onClick={() => setContextOpen(true)}
              className="mt-5 inline-flex items-center gap-2 rounded-md text-sm font-semibold text-brand underline decoration-brand-soft decoration-2 underline-offset-[6px] transition-colors hover:text-brand-dark hover:decoration-brand cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-light focus-visible:ring-offset-4 focus-visible:ring-offset-background"
            >
              <BookOpen className="h-4 w-4" aria-hidden="true" />
              {home.businessContext.triggerLabel}
            </button>

            {/* CTA */}
            <Link
              href="/chat"
              className="group mt-10 flex max-w-xl items-center justify-between gap-6 rounded-2xl border border-border bg-surface px-6 py-5 shadow-sm transition-all hover:border-brand-light hover:shadow-md cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-light sm:px-7 sm:py-6"
            >
              <div>
                <h2 className="font-display text-2xl font-extrabold text-primary sm:text-3xl">
                  {home.cta.title}
                </h2>
                <p className="mt-1 text-sm text-muted-foreground sm:text-base">
                  {home.cta.subtitle}
                </p>
              </div>
              {/* A diamond, the shape of a sign that asks for attention, which
                  turns warning yellow on hover. */}
              <span className="mr-2 flex h-11 w-11 shrink-0 rotate-45 items-center justify-center rounded-md bg-brand transition-colors group-hover:bg-accent sm:h-12 sm:w-12">
                <ArrowRight
                  className="h-5 w-5 -rotate-45 text-white transition-all group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-primary"
                  aria-hidden="true"
                />
              </span>
            </Link>
          </div>
        </div>

        {/* The route the sign points at */}
        <div
          className={`lg:col-span-5 lg:mt-2 delay-300 ${reveal("opacity-100 translate-x-0", "opacity-0 translate-x-6")}`}
        >
          <div className="rounded-2xl border border-border bg-surface px-3 py-4 shadow-sm sm:px-5 sm:py-6">
            {/* The four business threads. Same four on the agent's welcome
                screen, from the same `[[capabilities]]`. */}
            <CapabilityRoute capabilities={capabilities} />
          </div>
        </div>
      </div>

      {/* What the data is and what it runs on: reference facts, set as data. */}
      <div
        className={`mx-auto mt-14 flex max-w-6xl flex-col gap-4 border-t border-border pt-6 lg:flex-row lg:items-center lg:justify-between delay-500 ${reveal("opacity-100", "opacity-0")}`}
      >
        <p className="max-w-2xl font-mono text-xs leading-relaxed text-muted-foreground">
          {home.blurb}
        </p>
        <ul className="flex flex-wrap gap-2">
          {home.techStack.map((tech) => (
            <li
              key={tech}
              className="rounded-md border border-border bg-surface px-2.5 py-1 font-mono text-xs text-primary/80"
            >
              {tech}
            </li>
          ))}
        </ul>
      </div>

      <BusinessContextModal
        open={contextOpen}
        onClose={() => setContextOpen(false)}
        labels={home.businessContext}
        markdown={businessContext}
      />
    </section>
  )
}
