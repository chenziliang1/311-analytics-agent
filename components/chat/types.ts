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

/** A result too long for the bubble, whole, for the CSV download. */
export type TableData = {
  columns: string[]
  rows: Cell[][]
  row_count: number
  truncated: boolean
  sql?: string | null
}

export type LensUIMessage = UIMessage<
  never,
  { status: StatusEvent; turn: TurnData; table: TableData }
>
