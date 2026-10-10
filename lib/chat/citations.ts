/**
 * The agent's citation markers, `[KB:metric/sla_breach_rate]`, on their way
 * to the chip that opens the cited document (components/chat/Citation.tsx).
 *
 * Pure functions, kept out of the component so the one piece of the
 * citation path that needs no browser is tested without one.
 */

/** A marker: `[KB:<kind>/<key>]`, the id as agent_app/knowledge.py spells it. */
export const CITATION = /\[KB:([a-z_]+\/[A-Za-z0-9_\-]+)\]/g;

/**
 * The href a marker becomes, which the Markdown renderer recognises. A
 * fragment, like `#ask:`, on purpose: react-markdown's default URL transform
 * drops any scheme it does not know, so a `kb:` href arrived empty and the
 * citation rendered as an ordinary link that opened the page in a new tab.
 */
export const CITATION_SCHEME = "#kb:";

/** Every marker in `text` rewritten as a Markdown link the renderer turns into a chip. */
export function linkCitations(text: string): string {
  return text.replace(CITATION, (_match, id: string) => `[${id}](<${CITATION_SCHEME}${id}>)`);
}

/** The source id behind a link href, or null when the link is not a citation. */
export function citationId(href: string | undefined): string | null {
  if (!href || !href.startsWith(CITATION_SCHEME)) return null;
  const id = href.slice(CITATION_SCHEME.length);
  return id ? id : null;
}

/** `metric/sla_breach_rate` -> `{ kind: "metric", key: "sla_breach_rate" }`. */
export function splitCitationId(id: string): { kind: string; key: string } {
  const slash = id.indexOf("/");
  return slash > 0 ? { kind: id.slice(0, slash), key: id.slice(slash + 1) } : { kind: "", key: id };
}
