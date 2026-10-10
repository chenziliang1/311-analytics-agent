"use client";

import { BookOpen, X } from "lucide-react";
import { useState } from "react";

import type { SiteConfig } from "@/lib/site/schema";

/** What /api/source returns: the MCP server's read_source output. */
type SourceDoc = {
  status: string;
  id: string;
  title?: string | null;
  text?: string | null;
  warning?: string | null;
};

/**
 * A `[KB:metric/sla_breach_rate]` marker in an answer, as a chip. Opening it
 * fetches the document the agent cited, read as the signed-in user on the
 * chat's own session, and shows it in place. The marker is the agent's
 * claim; the chip is how the reader checks it.
 */
export function Citation({ id, chatId, site }: { id: string; chatId?: string; site: SiteConfig }) {
  const [open, setOpen] = useState(false);
  const [doc, setDoc] = useState<SourceDoc | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "failed">("idle");
  const slash = id.indexOf("/");
  const kind = slash > 0 ? id.slice(0, slash) : "";
  const key = slash > 0 ? id.slice(slash + 1) : id;

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (!next || doc || state === "loading") return;
    setState("loading");
    try {
      const chat = chatId ? `?chat=${encodeURIComponent(chatId)}` : "";
      const response = await fetch(`/api/source/${id}${chat}`);
      const data = (await response.json()) as SourceDoc;
      if (!response.ok || data.status !== "ok") throw new Error(data.warning ?? data.status);
      setDoc(data);
      setState("idle");
    } catch {
      setState("failed");
    }
  };

  // The document starts with its own "# Title" line; the panel has a header.
  const body = (doc?.text ?? "").replace(/^# .*\n+/, "");

  return (
    <span className="relative inline-block align-baseline">
      <button
        type="button"
        onClick={toggle}
        title={id}
        aria-expanded={open}
        className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 font-mono text-[0.75em] leading-none text-primary transition-colors hover:bg-accent/20"
      >
        <BookOpen className="h-3 w-3 shrink-0" aria-hidden />
        {kind && <span className="text-primary/50">{kind}/</span>}
        {key}
      </button>
      {open && (
        <span className="absolute left-0 top-full z-20 mt-1 block w-80 max-w-[90vw] rounded-lg border border-border bg-surface p-3 text-left text-sm font-sans shadow-lg">
          <span className="mb-2 flex items-start justify-between gap-2">
            <span className="font-display font-bold">{doc?.title ?? id}</span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label={site.chat.citation.close}
              className="rounded p-0.5 text-primary/60 hover:text-primary"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </span>
          {state === "loading" && <span className="text-primary/70">{site.chat.citation.loading}</span>}
          {state === "failed" && <span className="text-primary/70">{site.chat.citation.failed}</span>}
          {doc && <span className="block whitespace-pre-wrap text-xs leading-relaxed text-primary/90">{body}</span>}
        </span>
      )}
    </span>
  );
}
