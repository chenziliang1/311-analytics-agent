"use client"

import { ChartFigure } from "./chart/ChartFigure"
import { parseChartSpec } from "./chart/schema"
import { CodeBlock } from "./CodeBlock"
import type { BlockProps } from "./types"

/**
 * Renders a ```chart block as a bar or line chart, falling back to CodeBlock.
 *
 * Unlike Mermaid this needs no dynamic import: the renderer is hand written SVG
 * with no charting library behind it, so there is no bundle worth deferring.
 *
 * Falling back is routine rather than exceptional. While a response streams the
 * JSON is truncated and cannot parse, so the reader sees the raw payload until
 * the closing fence lands and it turns into a chart.
 */
export function ChartBlock({ source, language }: BlockProps) {
  const spec = parseChartSpec(source)
  if (!spec) return <CodeBlock source={source} language={language} />
  return <ChartFigure spec={spec} />
}
