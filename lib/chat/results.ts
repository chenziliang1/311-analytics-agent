/**
 * Query results the answer refers to by id (agent_app/results.py).
 *
 * The agent writes `[RESULT:r1]` on a line of its own where a result's rows
 * belong; the rows arrive with the answer as a `data-table` part carrying
 * the same `result_id`, and the paragraph is drawn as that table. The agent
 * never retypes rows, so what the reader sees is what the query returned.
 *
 * Plain TypeScript with no imports, so the Node test runner can load it as is.
 */

import type { Cell } from "./csv"

/** The id in a paragraph that is exactly one reference, else null. */
export function resultRef(text: string): string | null {
  const m = /^\s*\[RESULT:([A-Za-z0-9_-]+)\]\s*$/.exec(text)
  return m ? m[1] : null
}

/** A cell as the table shows it: what the query returned, not a reformatting. */
export function cellText(cell: Cell): string {
  if (cell === null || cell === undefined) return ""
  if (typeof cell === "number") {
    // The agent's prose rounds; the table shows the value, to a readable
    // precision, so a reader can check the prose against it.
    return Number.isInteger(cell) ? String(cell) : String(Number(cell.toPrecision(6)))
  }
  return String(cell)
}

/** `{shown}` and `{rows}` in a label replaced by the counts. */
export function rowsShownLabel(label: string, shown: number, rows: number): string {
  return label.replace("{shown}", shown.toLocaleString("en-US")).replace("{rows}", rows.toLocaleString("en-US"))
}
