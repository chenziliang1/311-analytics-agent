/**
 * The single shape every fenced-block renderer receives.
 *
 * Each ```lang block in the agent's Markdown is parsed into this, then handed
 * to whichever component `index.ts` resolves for that language.
 */
export interface BlockProps {
  /** Raw text between the fences, exactly as the model emitted it, minus the trailing newline. */
  source: string
  /** The language tag on the opening fence, such as "mermaid". Empty string when none was given. */
  language: string
}
