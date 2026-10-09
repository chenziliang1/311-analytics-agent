import { cn } from "@/lib/utils"

import type { BlockProps } from "./types"

/**
 * A plain fenced code block.
 *
 * It is also the fallback for the whole block layer. An unknown language, a
 * renderer that was removed, a Mermaid syntax error, and a block that is still
 * streaming all end up here. That is why it does nothing that could throw.
 */
export function CodeBlock({ source, language }: BlockProps) {
  return (
    <pre
      className={cn(
        "bg-muted border border-border",
        "p-4 rounded-lg my-3 overflow-x-auto"
      )}
    >
      <code
        className={cn(
          "text-[0.8rem] leading-relaxed font-mono text-primary",
          language && `language-${language}`
        )}
      >
        {source}
      </code>
    </pre>
  )
}
