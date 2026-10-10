"use client";

import { cellText, rowsShownLabel } from "@/lib/chat/results";

import type { TableData } from "./types";

/** Rows drawn in place; past this the CSV button under the answer has the rest. */
const SHOWN_ROWS = 100;

/**
 * A query result where the answer wrote `[RESULT:<id>]`: the rows as the
 * query returned them, in the same table styling as Markdown gets, so the
 * reader cannot tell a referenced table from a typed one except that this
 * one is never wrong.
 */
export function ResultTable({ table, rowsShown }: { table: TableData; rowsShown: string }) {
  const rows = table.rows.slice(0, SHOWN_ROWS);
  return (
    <div className="my-3">
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="min-w-full border-collapse">
          <thead className="bg-brand text-white">
            <tr>
              {table.columns.map((column, i) => (
                <th
                  key={i}
                  className="px-3 py-2 text-left font-display text-xs font-bold uppercase tracking-[0.04em] whitespace-nowrap"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} className="px-3 py-2 text-sm border-t border-border tabular-nums whitespace-nowrap">
                    {cellText(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.row_count > rows.length && (
        <p className="mt-1 text-xs text-muted-foreground">{rowsShownLabel(rowsShown, rows.length, table.row_count)}</p>
      )}
    </div>
  );
}
