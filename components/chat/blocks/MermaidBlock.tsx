"use client"

import { useEffect, useId, useState } from "react"

import { CodeBlock } from "./CodeBlock"
import type { BlockProps } from "./types"

/**
 * The mermaid bundle is close to 1MB and most conversations contain no diagram
 * at all, so it is imported only when the page actually meets its first
 * ```mermaid block. Every later block reuses this one promise.
 */
let mermaidPromise: Promise<typeof import("mermaid").default> | null = null

function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        // Diagram source comes from the LLM, which is in turn relaying database
        // content. "strict" runs mermaid's output through DOMPurify and
        // disables HTML labels, and is the only reason rendering it via
        // dangerouslySetInnerHTML below is acceptable.
        securityLevel: "strict",
        // The app is light-only, so the diagram is too. It used to read a `dark`
        // class off <html> that nothing ever set. "base" is the one theme that
        // takes variables, which is how the nodes pick up the sign palette in
        // `tailwind.config.ts`; mermaid needs literal hex, not CSS variables.
        theme: "base",
        themeVariables: {
          primaryColor: "#E6F0EB",
          primaryBorderColor: "#00553F",
          primaryTextColor: "#0E2A21",
          secondaryColor: "#FFF4CC",
          tertiaryColor: "#F4F6F5",
          lineColor: "#5B6B64",
          textColor: "#0E2A21",
          edgeLabelBackground: "#FFFFFF",
          clusterBkg: "#F4F6F5",
          clusterBorder: "#D5DCD8",
        },
        fontFamily: "inherit",
      })
      return mermaid
    })
  }
  return mermaidPromise
}

/**
 * Renders a ```mermaid block as a diagram, falling back to CodeBlock.
 *
 * Falling back is the normal case, not the exceptional one: while a response
 * streams, the closing fence has not arrived yet, so every chunk hands this
 * component a longer half written diagram that cannot parse. The user sees code
 * first and a diagram the moment the block is complete.
 */
export function MermaidBlock({ source, language }: BlockProps) {
  const [svg, setSvg] = useState<string | null>(null)

  // Keeps two diagrams in one message from colliding. Mermaid uses this as a
  // DOM element id, and the raw useId value contains colons, which are illegal
  // in a selector, so they have to go.
  const domId = `mermaid-${useId().replace(/[^a-zA-Z0-9]/g, "")}`

  useEffect(() => {
    let cancelled = false

    loadMermaid()
      .then(async (mermaid) => {
        // parse throws on invalid or incomplete source, which is cheaper than
        // letting render fail and then cleaning up after it.
        await mermaid.parse(source)
        const { svg: rendered } = await mermaid.render(domId, source)
        if (!cancelled) setSvg(rendered)
      })
      .catch(() => {
        if (!cancelled) setSvg(null)
      })

    return () => {
      cancelled = true
      // A failed render can leave its temporary measuring node attached to the
      // document. Without this they accumulate for the life of the page.
      document.getElementById(domId)?.remove()
      document.getElementById(`d${domId}`)?.remove()
    }
  }, [source, domId])

  if (!svg) return <CodeBlock source={source} language={language} />

  return (
    <div
      className="my-3 p-4 flex justify-center overflow-x-auto rounded-lg border border-border bg-surface"
      // Safe because of securityLevel: "strict" above.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  )
}
