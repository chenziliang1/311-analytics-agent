import { Metadata } from "next"
import { generateSEOMetadata } from "@/lib/seo/generateMetadata"
import { loadBusinessContext, loadSite } from "@/lib/site/load"
import HomePageContent from "./HomePageContent"

/**
 * The Open Graph and Twitter titles used to be spelled out here, all four of
 * them equal to `title`. `generateSEOMetadata` already falls back to `title`
 * and `description`, so repeating them only created four more places for the
 * home page's title to disagree with itself.
 */
export async function generateMetadata(): Promise<Metadata> {
  const { seo } = loadSite()
  return generateSEOMetadata({
    title: seo.title,
    description: seo.description,
  })
}

export default function HomePage() {
  return <HomePageContent site={loadSite()} businessContext={loadBusinessContext()} />
}
