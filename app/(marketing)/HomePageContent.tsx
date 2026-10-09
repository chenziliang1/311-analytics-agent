"use client"

import Hero from "./_components/Hero"
import type { SiteConfig } from "@/lib/site/schema"

export default function HomePageContent({
  site,
  businessContext,
}: {
  site: SiteConfig
  businessContext: string
}) {
  return (
    <div className="min-h-screen bg-background text-primary font-body">
      <div className="relative">
        <Hero site={site} businessContext={businessContext} />

        <footer className="py-8 px-4 sm:px-6 lg:px-8 border-t border-border bg-background">
          <div className="max-w-7xl mx-auto text-center">
            <p className="text-sm text-muted-foreground">
              &copy; {new Date().getFullYear()} {site.brand.footerName}. All rights reserved.
            </p>
          </div>
        </footer>
      </div>
    </div>
  )
}
