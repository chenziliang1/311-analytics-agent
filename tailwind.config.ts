import type { Config } from "tailwindcss"

/* The tone, in one sentence: a 311 request travels a street before it travels
 * a database, so this app borrows the street's own public signage -- guide-sign
 * green for everything the city says, warning-sign yellow kept for the one
 * thing that should make you look twice, on a cool paper ground.
 *
 * Why green and not the navy most analytics tools wear: green is the colour of
 * a guide sign, the panel that tells you where to go next, which is this
 * agent's whole job. Yellow is the colour of a warning sign, so it appears
 * sparingly -- the wordmark's second half, an active stop on the route, the
 * agent thinking -- and never as a background.
 *
 * Type follows the same logic: Overpass is the open-source cut of Highway
 * Gothic, the face on North American road signs, and Public Sans is the US
 * government's own interface face. See `app/layout.tsx`.
 *
 * This app is light-only: there is no theme toggle, so no `darkMode` strategy
 * and no `dark:` variants anywhere in the tree. */
const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      /* The palette. Components name these instead of repeating a hex or a raw
       * Tailwind step, so a rebrand is this block and nothing else.
       *
       * Only the colours a rebrand actually changes live here. The neutral
       * scale stays as Tailwind's own `slate-*`: it is a vocabulary every React
       * developer already reads, and it survives a rebrand unchanged. */
      colors: {
        /** Ink. Headings, body text on paper, the user bubble, the tooltip. */
        primary: {
          DEFAULT: "#0E2A21",
          foreground: "#FFFFFF",
        },
        /** Guide-sign green. The nav bar, sign panels, links, the send button. */
        brand: {
          DEFAULT: "#00553F",
          dark: "#003D2D", // hover on a filled surface
          light: "#2F8A68", // focus rings and hover borders
          soft: "#9CC7B5", // resting edges that should read as green, quietly
          foreground: "#FFFFFF",
        },
        /** Warning-sign yellow. Rare by rule: see the note at the top. */
        accent: {
          DEFAULT: "#FFC20E",
          foreground: "#0E2A21",
        },
        /** Page and card backgrounds. */
        background: "#F4F6F5", // cool paper, the ground everything floats on
        foreground: "#0E2A21",
        surface: "#FFFFFF", // cards and panels, one step lighter than the page
        border: "#D5DCD8", // what `@apply border-border` resolves to
        muted: {
          DEFAULT: "#E8EDEA",
          foreground: "#5B6B64",
        },

        // shadcn/ui primitives. Kept whole because `components/ui/*` and any
        // component added with the shadcn CLI expect these exact names.
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
      },
      /* Three roles, three faces, loaded in `app/layout.tsx`:
       *
       *   display  Overpass       sign lettering: headings, the wordmark, titles
       *   body     Public Sans    running text and controls
       *   mono     Overpass Mono  data: counts, codes, table cells, SQL
       */
      fontFamily: {
        display: ["var(--font-display)", "sans-serif"],
        body: ["var(--font-body)", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      /* Radius is a size ladder, and each step names a role:
       *
       *   rounded-full   pill        route terminal, avatars' dots
       *   rounded-2xl    panel       guide sign, route panel, welcome card, modal
       *   rounded-xl     card        route stop, kickstart button, bubbles
       *   rounded-lg     block       code block, table, chart, avatars, buttons
       *   rounded-md/sm  inline      sign plates on icons, tags, chart tooltip
       *
       * Nothing is extended here: these are Tailwind's own steps. There used to
       * be a shadcn override (`lg: var(--radius)` and two more derived from it)
       * which lifted `lg` from 8px to 12px -- the same value Tailwind already
       * gives `xl`. Two class names for one value is why the ladder read as
       * arbitrary in the components. Nothing needed it: every shadcn
       * `rounded-md` in `components/ui/*` is overridden at its call site.
       */
    },
  },
  plugins: [require("tailwindcss-animate")],
}

export default config
