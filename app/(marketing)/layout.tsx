import Navigation from "@/app/_components/layouts/Navigation"
import { loadSite } from "@/lib/site/load"

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const { brand } = loadSite()

  return (
    <>
      <Navigation logoPrefix={brand.logoPrefix} logoAccent={brand.logoAccent} />
      {children}
    </>
  )
}
