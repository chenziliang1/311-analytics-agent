import { loadSite } from "@/lib/site/load"

/**
 * Site-wide metadata defaults.
 *
 * **Server only**: `loadSite` reads `data/site.toml` off disk.
 *
 * No `author`, `creator`, or profile image: this site presents a product, not a
 * person. Page metadata that names an individual (or uses their photo as the
 * social preview) does not belong here.
 */
const site = loadSite()

export const DEFAULT_SEO_CONFIG = {
  siteName: site.brand.displayName,
  locale: site.seo.locale,
  type: "website" as const,
}

export const COMMON_KEYWORDS = site.seo.keywords
