import type { UIMessage } from "ai"

import type { Cell } from "@/lib/chat/csv"

/**
 * The data parts the backend streams besides text (agent_app/ai_sdk_adapter.py).
 * The names must match the `data-<name>` types it writes.
 */

/** One step of a running turn. Transient: never stored on a message. */
export type StatusEvent = {
  /** thinking | warehouse_query | visualize | answering */
  phase: string
  /** The question as the agent passed it to the tool, when there is one. */
  detail: string | null
}

/** How the turn went, from its tool results. One per answer. */
export type TurnData = {
  status: "ok" | "clarify" | "error"
  warnings: string[]
  /**
   * Numbers in the answer that no query this turn returned
   * (agent_app/number_check.py). Absent on answers saved before it existed.
   */
  unverified?: string[]
  /** The numbers the turn's queries returned; sent back with the history. */
  values?: number[]
}

/**
 * One query result, whole. Drawn where the answer writes `[RESULT:<id>]`
 * (lib/chat/results.ts), and offered as a CSV when `download` is set.
 */
export type TableData = {
  /** Absent on answers saved before results had ids: those only download. */
  result_id?: string | null
  columns: string[]
  rows: Cell[][]
  row_count: number
  truncated: boolean
  /** Absent on older answers, which only carried tables worth downloading. */
  download?: boolean
  sql?: string | null
}

export type LensUIMessage = UIMessage<
  never,
  { status: StatusEvent; turn: TurnData; table: TableData }
>
