import type { ComponentType } from "react"

import { ChartBlock } from "./ChartBlock"
import { CodeBlock } from "./CodeBlock"
import { MermaidBlock } from "./MermaidBlock"
import type { BlockProps } from "./types"

/**
 * Fenced-block renderers, keyed by the language tag on the opening fence.
 *
 * This table is the switch: add a line to support a new kind of block, delete a
 * line to turn one off. Any language not listed here (```python and friends
 * included) falls through to CodeBlock.
 *
 * When adding one, do not stop at the frontend. The UI understanding a format
 * does not make the model produce it, so
 * `data/system-prompt.md` has to say when to emit the
 * block. That step is invisible from here and is the one people forget.
 */
const BLOCK_RENDERERS: Record<string, ComponentType<BlockProps>> = {
  mermaid: MermaidBlock,
  chart: ChartBlock,
}

/** Resolve a renderer for a language, falling back to plain code. */
export function getBlockRenderer(language: string): ComponentType<BlockProps> {
  return BLOCK_RENDERERS[language] ?? CodeBlock
}

export type { BlockProps }
