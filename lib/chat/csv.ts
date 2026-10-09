/**
 * Query rows as an RFC 4180 CSV file, built in the browser.
 *
 * The rows arrive with the answer (a `data-table` part), so the download
 * needs no second request and no storage bucket: the file is made from what
 * the page already holds.
 *
 * Plain TypeScript with no imports, so the Node test runner can load it as is.
 */

export type Cell = string | number | boolean | null | undefined

function quote(cell: Cell): string {
  if (cell === null || cell === undefined) return ""
  const text = String(cell)
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

export function toCsv(columns: string[], rows: Cell[][]): string {
  const lines = [columns, ...rows].map((row) => row.map(quote).join(","))
  // CRLF is what the RFC specifies and what Excel expects.
  return lines.join("\r\n") + "\r\n"
}

/** `{rows}` in a label replaced by the row count, formatted for reading. */
export function withRowCount(label: string, rows: number): string {
  return label.replace("{rows}", rows.toLocaleString("en-US"))
}
