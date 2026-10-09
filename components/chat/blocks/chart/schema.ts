/**
 * The wire format for a ```chart block, and a strict parser for it.
 *
 * The producer is an LLM, so the parser assumes hostile input: anything that is
 * not exactly the shape below returns null, and the caller shows the raw JSON
 * as a code block instead. Being strict here is what keeps a malformed chart
 * from reaching the renderer as `undefined` halfway down a draw call.
 *
 * Partial JSON during streaming fails the same way, which is the behaviour we
 * want. No separate "is it still streaming" signal is needed.
 */

/** Hard cap on series. Past four, colour alone stops separating them reliably. */
const MAX_SERIES = 4

/** Hard cap on categories. Past this a table communicates better than a chart. */
const MAX_CATEGORIES = 24

export interface ChartSeries {
  name: string
  values: number[]
}

export interface ChartSpec {
  type: "bar" | "line"
  title?: string
  yLabel?: string
  categories: string[]
  series: ChartSeries[]
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v)
}

function isNonEmptyString(v: unknown): v is string {
  return typeof v === "string" && v.trim().length > 0
}

/**
 * Parse the text inside a ```chart fence.
 *
 * Returns null for anything malformed, out of range, or still streaming.
 */
export function parseChartSpec(source: string): ChartSpec | null {
  let raw: unknown
  try {
    raw = JSON.parse(source)
  } catch {
    return null
  }

  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null
  const o = raw as Record<string, unknown>

  if (o.type !== "bar" && o.type !== "line") return null

  if (!Array.isArray(o.categories)) return null
  const categories = o.categories
  if (categories.length === 0 || categories.length > MAX_CATEGORIES) return null
  if (!categories.every(isNonEmptyString)) return null

  if (!Array.isArray(o.series)) return null
  if (o.series.length === 0 || o.series.length > MAX_SERIES) return null

  const series: ChartSeries[] = []
  for (const entry of o.series) {
    if (typeof entry !== "object" || entry === null) return null
    const s = entry as Record<string, unknown>
    if (!isNonEmptyString(s.name)) return null
    if (!Array.isArray(s.values)) return null
    // Every series must cover every category. A short row would otherwise draw
    // a bar of height NaN, which renders as nothing and looks like real data.
    if (s.values.length !== categories.length) return null
    if (!s.values.every(isFiniteNumber)) return null
    series.push({ name: s.name, values: s.values })
  }

  return {
    type: o.type,
    title: isNonEmptyString(o.title) ? o.title : undefined,
    yLabel: isNonEmptyString(o.yLabel) ? o.yLabel : undefined,
    categories,
    series,
  }
}
