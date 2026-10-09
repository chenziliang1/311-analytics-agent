import { BarChart3, Table2, Workflow } from "lucide-react"
import type { LucideIcon } from "lucide-react"

import type { RenderKind } from "@/lib/site/schema"

/**
 * The glyph on a kickstart button saying what pressing it will draw.
 *
 * Twelve buttons all look alike, and three of them produce a table, three a
 * diagram, and three a chart. Without a mark, the only way to find out which is
 * to press one and wait for a model to answer. The glyph turns the quota that
 * `data/site.toml` already enforces into something a reader can see.
 *
 * Not authored copy: the mapping lives here rather than as an emoji inside the
 * question text, so a question stays a sentence and the mark stays presentation.
 * `render = "free"` deliberately draws nothing -- a badge on every button marks
 * nothing at all.
 *
 * These three icons are imported directly rather than through `lib/icons.ts`.
 * That map is the set a dataset's `site.toml` may name; this mapping is fixed
 * by the renderer and is not the dataset author's to choose.
 */
const BADGES: Record<Exclude<RenderKind, "free">, { Icon: LucideIcon; label: string }> = {
  table: { Icon: Table2, label: "Answers with a table and the SQL" },
  flowchart: { Icon: Workflow, label: "Answers with a flowchart" },
  chart: { Icon: BarChart3, label: "Answers with a chart" },
}

export function RenderBadge({ render }: { render: RenderKind }) {
  if (render === "free") return null

  const { Icon, label } = BADGES[render]
  return (
    <Icon
      className="w-3.5 h-3.5 shrink-0 text-muted-foreground group-hover:text-brand transition-colors"
      role="img"
      aria-label={label}
    />
  )
}
