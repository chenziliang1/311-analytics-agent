"use client"

import { useState } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { Menu, X } from "lucide-react"

/** One entry in the top bar. */
interface NavItem {
  label: string
  href: string
}

/**
 * The bar is the same on every page, and its two entries are the app's two
 * pages. Not authored copy: a dataset swap rewrites what the app is about,
 * not how many pages it has.
 */
const DEFAULT_NAV_ITEMS: NavItem[] = [
  { label: "Home", href: "/" },
  { label: "Chat", href: "/chat" },
]

/** The GitHub mark, inline so it needs no icon package and no network. */
function GitHubMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden className={className}>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  )
}

export default function Navigation({
  items = DEFAULT_NAV_ITEMS,
  logoPrefix,
  logoAccent,
  repoUrl,
}: {
  items?: NavItem[]
  /** The wordmark, split so the two halves can be coloured differently. */
  logoPrefix: string
  logoAccent: string
  /** The source repository. Shown on every page: this is an open-source demo. */
  repoUrl: string
}) {
  const [isMenuOpen, setIsMenuOpen] = useState(false)
  const pathname = usePathname()

  const isActive = (href: string) => {
    if (href === "/") {
      return pathname === "/"
    }
    return pathname.startsWith(href)
  }

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 bg-brand shadow-[inset_0_-1px_0_rgba(255,255,255,0.12)]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between items-center h-16">
          {/* Logo / Brand */}
          <Link
            href="/"
            className="flex items-center gap-3 rounded-md text-white hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <span className="font-display text-xl font-extrabold tracking-tight">
              {logoPrefix}
              <span className="text-accent">{logoAccent}</span>
            </span>
          </Link>

          {/* Desktop Navigation */}
          <div className="hidden md:flex items-center gap-8">
            {items.map((item) => {
              const active = isActive(item.href)
              const isChat = item.href === "/chat"

              if (isChat) {
                return (
                  <span key={item.href} className="flex items-center gap-3">
                    <a
                      href={repoUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-2 font-display font-bold text-sm px-4 py-2 rounded-lg border border-white/40 text-white hover:bg-white hover:text-brand transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-brand"
                    >
                      <GitHubMark className="w-5 h-5" />
                      GitHub
                    </a>
                    <Link
                      href={item.href}
                      className="font-display font-bold text-sm px-5 py-2 bg-accent text-accent-foreground rounded-lg hover:bg-white transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-brand"
                    >
                      Start Chat
                    </Link>
                  </span>
                )
              }

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`
                    font-medium text-sm transition-colors cursor-pointer rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent
                    ${active
                      ? "text-accent"
                      : "text-white/75 hover:text-white"
                    }
                  `}
                >
                  {item.label}
                </Link>
              )
            })}
          </div>

          {/* Mobile: the repository is one tap away without opening the menu */}
          <div className="md:hidden flex items-center gap-1">
            <a
              href={repoUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="p-2 text-white hover:text-accent transition-colors cursor-pointer"
              aria-label="Source on GitHub"
            >
              <GitHubMark className="w-6 h-6" />
            </a>
            <button
              onClick={() => setIsMenuOpen(!isMenuOpen)}
              className="p-2 text-white/75 hover:text-white transition-colors cursor-pointer"
              aria-label="Toggle menu"
            >
              {isMenuOpen ? <X size={24} /> : <Menu size={24} />}
            </button>
          </div>
        </div>

        {/* Mobile Navigation Menu */}
        {isMenuOpen && (
          <div className="md:hidden border-t border-white/15">
            <div className="py-4 space-y-2">
              {items.map((item) => {
                const active = isActive(item.href)
                const isChat = item.href === "/chat"

                if (isChat) {
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className="block font-display font-bold text-base py-3 px-4 bg-accent text-accent-foreground text-center rounded-lg mx-4 cursor-pointer"
                      onClick={() => setIsMenuOpen(false)}
                    >
                      Start Chat
                    </Link>
                  )
                }

                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`
                      block font-medium text-base py-3 px-4 transition-colors cursor-pointer
                      ${active
                        ? "text-accent"
                        : "text-white/75 hover:text-white"
                      }
                    `}
                    onClick={() => setIsMenuOpen(false)}
                  >
                    {item.label}
                  </Link>
                )
              })}
              <a
                href={repoUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 font-medium text-base py-3 px-4 text-white/75 hover:text-white transition-colors cursor-pointer"
                onClick={() => setIsMenuOpen(false)}
              >
                <GitHubMark className="w-5 h-5" />
                Source on GitHub
              </a>
            </div>
          </div>
        )}
      </div>
    </nav>
  )
}
