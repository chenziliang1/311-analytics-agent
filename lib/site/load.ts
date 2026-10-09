import fs from "node:fs"
import path from "node:path"

import { parse } from "smol-toml"

import { ICONS } from "@/lib/icons"
import { REQUIRED_SITE_KEYS, type SiteConfig } from "./schema"

/**
 * Reads this app's copy off disk: `data/site.toml` and `data/business-context.md`.
 *
 * **Server only.** This touches the filesystem, so it may only be imported from
 * a server component or a route handler. Every page that uses it is statically
 * rendered, which means these files are read at build time and the strings are
 * baked into the output -- nothing here runs on a request.
 *
 * Why read TOML at all, instead of a checked-in `.ts`
 * ---------------------------------------------------
 * The copy belongs to the dataset, not to the frontend. `data/` is the one
 * directory a dataset swap rewrites, and keeping every authored artifact of a
 * dataset inside it -- the config, the copy, the explainer, the prompt, the
 * database -- is what makes a swap a directory replacement instead of a diff
 * scattered across the repo. A `.ts` file would put the copy back in the
 * frontend's tree and give the dataset two homes.
 *
 * The cost is that the compiler no longer checks this file, so the checks it
 * used to perform for free are performed here at load: `REQUIRED_SITE_KEYS`
 * for missing keys, `assertIconsExist` for icon names. Both throw.
 *
 * Everything is cached per process, so a page that asks twice pays once.
 */

/** Repo root. `process.cwd()` is where `next build` and `next dev` are invoked. */
const DIR_DATA = path.join(process.cwd(), "data")

const SITE_TOML = "site.toml"
const BUSINESS_CONTEXT_MD = "business-context.md"

let siteCache: SiteConfig | null = null
let contextCache: string | null = null

/** Follow a dotted path into parsed TOML. Returns `undefined` if any hop is missing. */
function dig(data: unknown, dotted: string): unknown {
  let node: unknown = data
  for (const part of dotted.split(".")) {
    if (typeof node !== "object" || node === null || !(part in node)) {
      return undefined
    }
    node = (node as Record<string, unknown>)[part]
  }
  return node
}

/**
 * Parse `data/site.toml`.
 *
 * Every key in `REQUIRED_SITE_KEYS` is checked on the way through. The check
 * exists because the alternative failure is silent: TOML is not type-checked,
 * so a missing key reaches a component as `undefined` and renders as an empty
 * heading on a live page. Failing the build with the key's name and the file's
 * path costs one pass over a short list.
 *
 * @throws If the file is missing, unparseable, or short a required key.
 */
export function loadSite(): SiteConfig {
  if (siteCache) return siteCache

  const file = path.join(DIR_DATA, SITE_TOML)
  if (!fs.existsSync(file)) {
    throw new Error(`Missing ${file}. This app cannot render without its copy.`)
  }

  const parsed = parse(fs.readFileSync(file, "utf-8")) as unknown

  const missing = REQUIRED_SITE_KEYS.filter((key) => dig(parsed, key) === undefined)
  if (missing.length > 0) {
    throw new Error(
      `${file} is missing required key(s): ${missing.join(", ")}. ` +
        `See REQUIRED_SITE_KEYS in lib/site/schema.ts for the full contract.`,
    )
  }

  const site = parsed as SiteConfig
  assertIconsExist(file, site)

  siteCache = site
  return site
}

/**
 * Check every `icon = "..."` in `site.toml` against `lib/icons.ts`.
 *
 * The compiler cannot do this. `SiteConfig` types these fields as `IconName`,
 * but the value arrives from a TOML file through a cast, so the type is a
 * promise about the file rather than a check of it. Unverified, a misspelled
 * icon silently renders the neutral fallback -- a card that looks fine, with
 * the wrong picture on it, which nobody notices.
 */
function assertIconsExist(file: string, site: SiteConfig): void {
  const known = Object.keys(ICONS)
  const bad = site.capabilities.filter((c) => !known.includes(c.icon))
  if (bad.length > 0) {
    const names = bad.map((c) => `capabilities: "${c.icon}"`).join(", ")
    throw new Error(
      `${file} names unknown icon(s): ${names}. ` +
        `Add them to ICONS in lib/icons.ts, or use one of: ${known.join(", ")}.`,
    )
  }
}

/**
 * The long-form explainer behind the home page, as raw Markdown.
 *
 * Returned otherwise unparsed on purpose: it is rendered by the same Markdown
 * component the chat uses, so a ```mermaid fence in the document draws a
 * diagram without this layer knowing anything about diagrams.
 *
 * HTML comments are stripped first. `react-markdown` runs without `rehype-raw`,
 * so it does not interpret them as HTML -- it prints them, and a note the
 * author wrote for the next author ends up as a paragraph of the overlay a
 * customer reads. Stripping here rather than banning comments in the file keeps
 * the authoring notes where they are useful.
 */
export function loadBusinessContext(): string {
  if (contextCache !== null) return contextCache

  const file = path.join(DIR_DATA, BUSINESS_CONTEXT_MD)
  if (!fs.existsSync(file)) {
    throw new Error(`Missing ${file}. The home page's explainer overlay reads it.`)
  }

  contextCache = fs
    .readFileSync(file, "utf-8")
    .replace(/<!--[\s\S]*?-->/g, "")
    .trim()
  return contextCache
}
