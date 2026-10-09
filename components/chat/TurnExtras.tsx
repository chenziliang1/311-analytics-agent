"use client";

import { AlertTriangle, Download, RotateCcw, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { toCsv, withRowCount } from "@/lib/chat/csv";
import type { SiteConfig } from "@/lib/site/schema";

import type { TableData } from "./types";

/**
 * The grey line under an answer that repeats its query's warnings. The
 * agent is told to pass them on too, but a prompt is a request; this line is
 * what makes sure a definition note or a truncation is never lost.
 */
export function NoteLine({ prefix, warnings }: { prefix: string; warnings: string[] }) {
  if (!warnings.length) return null;
  return (
    <p className="text-xs text-muted-foreground border-t border-border pt-2">
      <span className="font-semibold">{prefix}</span> {warnings.join(" ")}
    </p>
  );
}

/** Every row of a long result as a file, built in the browser. */
export function CsvDownload({ label, table, index }: { label: string; table: TableData; index: number }) {
  const download = () => {
    const blob = new Blob([toCsv(table.columns, table.rows)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `lens-result-${index + 1}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <button
      type="button"
      onClick={download}
      className="self-start inline-flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-semibold text-primary hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-light"
    >
      <Download className="w-3.5 h-3.5" aria-hidden />
      {withRowCount(label, table.row_count)}
    </button>
  );
}

/**
 * The red banner for a turn that could not be completed. The conversation
 * stays above it; the button asks the last question again.
 */
export function ErrorBanner({ site, onRetry }: { site: SiteConfig; onRetry?: () => void }) {
  return (
    <div className="max-w-3xl mx-auto w-full" role="alert">
      <div className="flex items-center justify-between gap-3 rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
        <span className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden />
          {site.chat.messages.errorBanner}
        </span>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 font-semibold hover:bg-destructive/15 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive"
          >
            <RotateCcw className="w-3.5 h-3.5" aria-hidden />
            {site.chat.messages.retry}
          </button>
        )}
      </div>
    </div>
  );
}

export type Rating = "up" | "down";

/**
 * Thumbs up or down on one answer. A thumbs-down
 * opens an optional comment first; either way the vote is stored for review
 * by `agent-app feedback`, where a thumbs-down can become a golden case.
 */
export function FeedbackButtons({
  site,
  onSubmit,
}: {
  site: SiteConfig;
  /** Resolves true once the vote is stored. */
  onSubmit: (rating: Rating, comment?: string) => Promise<boolean>;
}) {
  const [state, setState] = useState<"idle" | "commenting" | "sending" | "done" | "failed">("idle");
  const [comment, setComment] = useState("");
  const m = site.chat.messages;

  const send = async (rating: Rating, text?: string) => {
    setState("sending");
    setState((await onSubmit(rating, text)) ? "done" : "failed");
  };

  if (state === "done") {
    return <p className="text-xs text-muted-foreground">{m.feedbackThanks}</p>;
  }

  const iconButton =
    "inline-flex items-center justify-center w-7 h-7 rounded-md text-muted-foreground hover:text-primary hover:bg-muted transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-light";

  return (
    <div className="flex flex-col gap-2 border-t border-border pt-2">
      <div className="flex items-center gap-1">
        <button
          type="button"
          className={iconButton}
          aria-label={m.feedbackHelpful}
          title={m.feedbackHelpful}
          disabled={state === "sending"}
          onClick={() => send("up")}
        >
          <ThumbsUp className="w-3.5 h-3.5" aria-hidden />
        </button>
        <button
          type="button"
          className={iconButton}
          aria-label={m.feedbackNotHelpful}
          title={m.feedbackNotHelpful}
          aria-expanded={state === "commenting"}
          disabled={state === "sending"}
          onClick={() => setState("commenting")}
        >
          <ThumbsDown className="w-3.5 h-3.5" aria-hidden />
        </button>
        {state === "failed" && <span className="text-xs text-destructive ml-2">{m.feedbackFailed}</span>}
      </div>
      {state === "commenting" && (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            send("down", comment);
          }}
        >
          <input
            autoFocus
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder={m.feedbackCommentPlaceholder}
            maxLength={1000}
            className="flex-1 min-w-0 rounded-md border border-border bg-background px-2.5 py-1 text-xs text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-light"
          />
          <button
            type="submit"
            className="rounded-md border border-border px-2.5 py-1 text-xs font-semibold text-primary hover:bg-muted transition-colors"
          >
            {m.feedbackSend}
          </button>
        </form>
      )}
    </div>
  );
}
