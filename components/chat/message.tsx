"use client";

import { motion } from "framer-motion";
import { User } from "lucide-react";
import { useState } from "react";

import { AssistantAvatar } from "./AssistantAvatar";
import { Markdown } from "./markdown";
import { CsvDownload, FeedbackButtons, NoteLine, type Rating } from "./TurnExtras";
import type { LensUIMessage, StatusEvent } from "./types";
import { cn } from "@/lib/utils";
import type { SiteConfig } from "@/lib/site/schema";

/**
 * Collapsible component for displaying reasoning/thinking content.
 * Shows a summary by default, expands to show full content on click.
 */
const ReasoningBlock = ({ text }: { text: string }) => {
  const [isExpanded, setIsExpanded] = useState(false);

  // Truncate text for preview (first 100 chars)
  const previewText = text.length > 100 ? text.slice(0, 100) + "..." : text;

  return (
    <div className="border border-accent/60 rounded-lg bg-accent/10 overflow-hidden">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-accent/20 transition-colors"
      >
        <svg
          className={cn(
            "w-4 h-4 text-primary/70 transition-transform",
            isExpanded && "rotate-90"
          )}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="font-display text-xs font-bold text-primary">
          Thinking
        </span>
        {!isExpanded && (
          <span className="text-xs text-primary/60 truncate flex-1">
            {previewText}
          </span>
        )}
      </button>
      {isExpanded && (
        <div className="px-3 py-2 border-t border-accent/60">
          <div className="font-mono text-xs text-primary/80 whitespace-pre-wrap">
            {text}
          </div>
        </div>
      )}
    </div>
  );
};

export const PreviewMessage = ({
  site,
  message,
  append,
  onFeedback,
}: {
  site: SiteConfig;
  message: LensUIMessage;
  append?: (message: any) => Promise<string | null | undefined>;
  /** Present once the answer is complete; absent while it streams. */
  onFeedback?: (rating: Rating, comment?: string) => Promise<boolean>;
}) => {
  const tables = message.parts.filter((part) => part.type === "data-table");
  return (
    <motion.div
      className="w-full mx-auto max-w-3xl group/message"
      initial={{ y: 5, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      data-role={message.role}
    >
      <div
        className={cn(
          "flex gap-3 w-full",
          message.role === "user" ? "justify-end" : "justify-start"
        )}
      >
        {message.role === "assistant" && <AssistantAvatar />}

        <div
          className={cn(
            "flex flex-col gap-2 min-w-0 max-w-[85%] sm:max-w-[75%] rounded-xl p-4",
            message.role === "user"
              ? "bg-primary text-white"
              : "bg-surface border border-border shadow-sm"
          )}
        >
          {/* AI SDK v5: Use parts instead of content */}
          {message.parts && message.parts.length > 0 && (
            <div className="flex flex-col gap-4">
              {message.parts.map((part: any, index: number) => {
                // Render reasoning/thinking content
                if (part.type === 'reasoning' && part.text) {
                  return (
                    <ReasoningBlock key={index} text={part.text} />
                  );
                }
                // Render text content
                if (part.type === 'text' && part.text) {
                  return (
                    <div
                      key={index}
                      className={cn(
                        message.role === "assistant"
                          ? "text-primary"
                          : "text-white"
                      )}
                    >
                      <Markdown
                        onQuestionClick={(question) => {
                          append?.({
                            role: 'user',
                            content: question,
                          });
                        }}
                      >
                        {part.text}
                      </Markdown>
                    </div>
                  );
                }
                if (part.type === "data-turn") {
                  return (
                    <NoteLine
                      key={index}
                      prefix={site.chat.messages.notePrefix}
                      warnings={part.data.warnings}
                    />
                  );
                }
                if (part.type === "data-table") {
                  return (
                    <CsvDownload
                      key={index}
                      label={site.chat.messages.downloadCsv}
                      table={part.data}
                      index={tables.indexOf(part)}
                    />
                  );
                }
                return null;
              })}
            </div>
          )}
          {message.role === "assistant" && onFeedback && (
            <FeedbackButtons site={site} onSubmit={onFeedback} />
          )}
        </div>

        {/* User Avatar - Right side */}
        {message.role === "user" && (
          <div className="w-8 h-8 flex items-center justify-center border border-border shrink-0 rounded-lg bg-muted">
            <User className="w-4 h-4 text-muted-foreground" aria-label="You" />
          </div>
        )}
      </div>
    </motion.div>
  );
};

/** The spinner's line for one step of a running turn. */
function statusLabel(site: SiteConfig, status: StatusEvent | null): string {
  const labels: Record<string, string> = {
    thinking: site.chat.status.thinking,
    warehouse_query: site.chat.status.warehouseQuery,
    visualize: site.chat.status.visualize,
    answering: site.chat.status.answering,
  };
  return labels[status?.phase ?? "thinking"] ?? site.chat.status.thinking;
}

/**
 * The bubble that stands in for the answer while a turn runs: what the agent
 * is doing right now, and, past the slow threshold, why it is taking long
 * and a way out.
 */
export const ThinkingMessage = ({
  site,
  status,
  busy,
  slow,
  onStopAndRetry,
}: {
  site: SiteConfig;
  status: StatusEvent | null;
  /** The one automatic retry after a 429 is waiting. */
  busy: boolean;
  slow: boolean;
  onStopAndRetry: () => void;
}) => {
  const role = "assistant";
  const label = busy ? site.chat.status.busyRetrying : `${statusLabel(site, status)}...`;

  return (
    <motion.div
      className="w-full mx-auto max-w-3xl group/message"
      initial={{ y: 5, opacity: 0 }}
      animate={{ y: 0, opacity: 1, transition: { delay: 0.3 } }}
      data-role={role}
    >
      <div className="flex gap-3 w-full justify-start">
        <AssistantAvatar />

        <div className="bg-surface border border-border rounded-xl p-4 flex flex-col gap-2 min-w-0 max-w-[85%] sm:max-w-[75%]">
          <div className="flex items-center gap-2 text-primary/70" aria-live="polite">
            <span className="inline-block w-2.5 h-2.5 shrink-0 rotate-45 rounded-[2px] border-2 border-brand bg-accent motion-safe:animate-pulse" />
            <span className="font-display text-sm font-bold">{label}</span>
          </div>
          {!busy && status?.detail && (
            <p className="text-xs text-muted-foreground truncate" title={status.detail}>
              {status.detail}
            </p>
          )}
          {slow && (
            <div className="flex flex-col gap-2 border-t border-border pt-2">
              <p className="text-xs text-muted-foreground">{site.chat.status.slow}</p>
              <button
                type="button"
                onClick={onStopAndRetry}
                className="self-start rounded-md border border-border px-2.5 py-1 text-xs font-semibold text-primary hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-light"
              >
                {site.chat.status.stopAndRetry}
              </button>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
};
