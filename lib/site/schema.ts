import type { IconName } from "@/lib/icons"

/**
 * The shape of `data/site.toml`, as TypeScript sees it.
 *
 * This file used to be `config/site.ts`: a TypeScript literal, which the
 * compiler checked for free. TOML gives that up on purpose, so that every
 * authored artifact of a dataset sits in one directory that can be swapped as
 * a unit, and so that the copy can carry real comments next to the sentence
 * they explain. What the compiler used to catch is now caught by `load.ts` at
 * startup: `REQUIRED_SITE_KEYS` for missing keys, `assertIconsExist` for icon
 * names. Both throw rather than render a blank.
 *
 * `icon` is still typed against the names `lib/icons.ts` exports, which is what
 * makes a component's use of an icon name safe. The value arriving from TOML is
 * what needs the runtime check.
 */

/**
 * One of this app's four business lines.
 *
 * Rendered twice, as a card on the home page and again on the agent's welcome
 * screen, from this one entry. It used to be two arrays -- `home.capabilities`
 * and `chat.insights` -- naming the same four things in two vocabularies, and
 * they had already drifted: the second thread was "Portfolio Concentration" on
 * one screen and "Portfolio Health" on the other, with a different icon and no
 * tooltip at all.
 */
export interface Capability {
  icon: IconName
  /** The concept name. Precise to an insider, opaque to everyone else. */
  title: string
  /** One plain line saying what the concept buys you. */
  subtitle: string
  /** Two or three plain sentences defining the title, for a reader outside the domain. */
  tooltip: string
}

/** What a kickstart question makes the UI draw. */
export type RenderKind = "table" | "flowchart" | "chart" | "free"

/** One kickstart question, shown as a button above the composer. */
export interface SuggestedAction {
  /**
   * The block this question produces, shown as a glyph on the button.
   *
   * A promise to the reader about what pressing it will look like. What
   * actually delivers the block is a literal trigger phrase inside `action`;
   * `mise run check` refuses to let the two disagree.
   */
  render: RenderKind
  title: string
  label: string
  /** The full sentence actually sent to the agent when the button is pressed. */
  action: string
}

export interface SiteConfig {
  brand: {
    displayName: string
    /** The wordmark, split so the two halves can be coloured differently. */
    logoPrefix: string
    logoAccent: string
    footerName: string
  }
  seo: {
    locale: string
    title: string
    description: string
    keywords: string[]
  }
  home: {
    tagline: string
    description: string
    /** One line describing what the data covers. */
    blurb: string
    techStack: string[]
    headline: { prefix: string; accent: string }
    cta: { title: string; subtitle: string }
    /** Labels for the overlay that renders `data/business-context.md`. */
    businessContext: { triggerLabel: string; title: string; subtitle: string }
  }
  /** The four business lines, shown on both the home page and the agent. */
  capabilities: Capability[]
  chat: {
    /** What the assistant calls itself in the chat UI. */
    agent: string
    welcomeSubtitle: string
    tryExamples: string
    suggestionsHint: string
    inputPlaceholder: string
    messages: {
      /** The whole sentence in the banner above the conversation, printed as-is. */
      welcomeMessage: string
      /** User submitted while a reply was still streaming. */
      waitForResponse: string
      /** The red banner after a turn whose queries all failed. */
      errorBanner: string
      retry: string
      /** Starts the line repeating a query's warnings under an answer. */
      notePrefix: string
      /** Starts the line listing numbers in an answer that no query returned. */
      unverifiedPrefix: string
      /** The CSV button's label; `{rows}` becomes the row count. */
      downloadCsv: string
      newChat: string
      /** `{name}` becomes the signed-in user. Shown only when sign-in is on. */
      signedInAs: string
      signOut: string
      /** The thumbs under an answer, and what they say after a vote. */
      feedbackHelpful: string
      feedbackNotHelpful: string
      feedbackCommentPlaceholder: string
      feedbackSend: string
      feedbackThanks: string
      feedbackFailed: string
    }
    /** The spinner's line for each step a turn reports while it runs. */
    status: {
      thinking: string
      warehouseQuery: string
      visualize: string
      answering: string
      /** Shown once a turn has run past the slow threshold. */
      slow: string
      stopAndRetry: string
      /** The one automatic retry after a 429. */
      busyRetrying: string
    }
    suggestedActions: SuggestedAction[]
  }
}

/**
 * Every key `data/site.toml` must declare, as dotted paths.
 *
 * Checked by `load.ts` when Next.js reads the file, so a missing key fails the
 * build with the key's name instead of rendering an empty heading on a live
 * page. TOML is not type-checked, so this list is the only thing standing
 * between a typo and an `undefined` in production.
 */
export const REQUIRED_SITE_KEYS: readonly string[] = [
  "brand.displayName",
  "brand.logoPrefix",
  "brand.logoAccent",
  "brand.footerName",
  "seo.locale",
  "seo.title",
  "seo.description",
  "seo.keywords",
  "home.tagline",
  "home.description",
  "home.blurb",
  "home.techStack",
  "home.headline.prefix",
  "home.headline.accent",
  "home.cta.title",
  "home.cta.subtitle",
  "home.businessContext.triggerLabel",
  "home.businessContext.title",
  "home.businessContext.subtitle",
  "capabilities",
  "chat.agent",
  "chat.welcomeSubtitle",
  "chat.tryExamples",
  "chat.suggestionsHint",
  "chat.inputPlaceholder",
  "chat.suggestedActions",
  "chat.messages.welcomeMessage",
  "chat.messages.waitForResponse",
  "chat.messages.errorBanner",
  "chat.messages.retry",
  "chat.messages.notePrefix",
  "chat.messages.unverifiedPrefix",
  "chat.messages.downloadCsv",
  "chat.messages.newChat",
  "chat.messages.signedInAs",
  "chat.messages.signOut",
  "chat.messages.feedbackHelpful",
  "chat.messages.feedbackNotHelpful",
  "chat.messages.feedbackCommentPlaceholder",
  "chat.messages.feedbackSend",
  "chat.messages.feedbackThanks",
  "chat.messages.feedbackFailed",
  "chat.status.thinking",
  "chat.status.warehouseQuery",
  "chat.status.visualize",
  "chat.status.answering",
  "chat.status.slow",
  "chat.status.stopAndRetry",
  "chat.status.busyRetrying",
]
