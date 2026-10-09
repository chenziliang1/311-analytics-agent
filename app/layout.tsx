import type React from "react"
import type { Metadata, Viewport } from "next"
import { Overpass, Overpass_Mono, Public_Sans } from "next/font/google"
import { generateSEOMetadata } from "@/lib/seo/generateMetadata"
import { loadSite } from "@/lib/site/load"
import "./globals.css"

// Overpass is the open-source cut of Highway Gothic, the lettering on North
// American road signs; Public Sans is the US government's own interface face.
// Why a 311 tool dresses as street signage is written up at the top of
// `tailwind.config.ts`.
const display = Overpass({
  subsets: ["latin"],
  variable: "--font-display",
  weight: ["600", "700", "800", "900"],
})
const body = Public_Sans({
  subsets: ["latin"],
  variable: "--font-body",
  weight: ["400", "500", "600", "700"],
})
const mono = Overpass_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  weight: ["400", "600"],
})

const { seo } = loadSite()

export const metadata: Metadata = {
  ...generateSEOMetadata({
    title: seo.title,
    description: seo.description,
  }),
  icons: {
    icon: [
      { url: '/icon.png', type: 'image/png' },
    ],
    shortcut: '/icon.png',
    apple: '/icon.png',
  },
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body className="font-body antialiased">{children}</body>
    </html>
  )
}
