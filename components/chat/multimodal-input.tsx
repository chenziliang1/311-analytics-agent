"use client";

import type { ChatRequestOptions, UIMessage } from "ai";
import { motion } from "framer-motion";
import type React from "react";
import { useRef, useEffect, useCallback } from "react";
import { toast } from "sonner";
import { useLocalStorage, useWindowSize } from "usehooks-ts";

import { cn } from "@/lib/utils";

import { ArrowUpIcon, StopIcon } from "./icons";
import { RenderBadge } from "./RenderBadge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { SiteConfig } from "@/lib/site/schema";

export function MultimodalInput({
  site,
  input,
  setInput,
  isLoading,
  stop,
  messages,
  append,
  handleSubmit,
  className,
}: {
  site: SiteConfig;
  input: string;
  setInput: (value: string) => void;
  isLoading: boolean;
  stop: () => void;
  messages: Array<UIMessage>;
  append: (
    message: any,
    chatRequestOptions?: ChatRequestOptions,
  ) => Promise<string | null | undefined>;
  handleSubmit: (
    event?: {
      preventDefault?: () => void;
    },
    chatRequestOptions?: ChatRequestOptions,
  ) => void;
  className?: string;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { width } = useWindowSize();

  useEffect(() => {
    if (textareaRef.current) {
      adjustHeight();
    }
  }, []);

  const adjustHeight = () => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight + 2}px`;
    }
  };

  const [localStorageInput, setLocalStorageInput] = useLocalStorage(
    "input",
    "",
  );

  useEffect(() => {
    if (textareaRef.current) {
      const domValue = textareaRef.current.value;
      const finalValue = domValue || localStorageInput || "";
      setInput(finalValue);
      adjustHeight();
    }
  }, []);

  useEffect(() => {
    setLocalStorageInput(input);
  }, [input, setLocalStorageInput]);

  const handleInput = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(event.target.value);
    adjustHeight();
  };

  const submitForm = useCallback(() => {
    handleSubmit(undefined, {});
    setLocalStorageInput("");

    if (width && width > 768) {
      textareaRef.current?.focus();
    }
  }, [handleSubmit, setLocalStorageInput, width]);

  return (
    <div className="relative w-full flex flex-col gap-3">
      {/* Suggested Questions */}
      {messages.length === 0 && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <span className="h-px flex-1 bg-border" aria-hidden="true" />
            <p className="font-display text-xs font-bold uppercase tracking-[0.08em] text-muted-foreground">
              {site.chat.suggestionsHint}
            </p>
            <span className="h-px flex-1 bg-border" aria-hidden="true" />
          </div>

          <div className="max-h-[30dvh] sm:max-h-[280px] overflow-y-auto">
            <div className="grid sm:grid-cols-2 gap-2 w-full pr-1">
              {site.chat.suggestedActions.map((suggestedAction, index) => (
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 20 }}
                  transition={{ delay: 0.05 * index }}
                  key={`suggested-action-${suggestedAction.title}-${index}`}
                >
                  <Button
                    variant="ghost"
                    onClick={async () => {
                      append({
                        role: "user",
                        content: suggestedAction.action,
                      });
                    }}
                    className="group text-left border border-border bg-surface hover:bg-surface hover:text-primary hover:border-brand-light hover:shadow-sm focus-visible:ring-brand-light px-4 py-3 text-sm flex-1 gap-1 flex-col w-full h-auto justify-start items-start whitespace-normal transition-all cursor-pointer rounded-xl"
                  >
                    <span className="font-display text-sm font-bold text-primary">
                      {suggestedAction.title}
                    </span>
                    {/* The glyph says what pressing this will draw. Nine of the
                        twelve produce a table, a flowchart, or a chart; without
                        a mark the only way to find out is to press one. */}
                    <span className="flex items-center gap-1.5 text-xs text-brand group-hover:text-brand-dark leading-snug transition-colors">
                      <RenderBadge render={suggestedAction.render} />
                      {suggestedAction.label}
                    </span>
                  </Button>
                </motion.div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* User Message Input Form */}
      <div className="relative">
        <Textarea
          ref={textareaRef}
          placeholder={site.chat.inputPlaceholder}
          value={input}
          onChange={handleInput}
          className={cn(
            "min-h-[24px] max-h-[calc(75dvh)] overflow-hidden resize-none !text-base",
            "bg-surface border border-border rounded-xl",
            "focus:border-brand-light focus:ring-brand-light focus:ring-2",
            "text-primary placeholder:text-muted-foreground",
            "pr-12",
            className,
          )}
          rows={3}
          autoFocus
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();

              if (isLoading) {
                toast.error(site.chat.messages.waitForResponse);
              } else {
                submitForm();
              }
            }
          }}
        />

        {isLoading ? (
          <Button
            className="p-2 h-fit absolute bottom-2 right-2 bg-surface border border-border hover:bg-muted text-primary cursor-pointer rounded-lg"
            onClick={(event) => {
              event.preventDefault();
              stop();
            }}
          >
            <StopIcon size={16} />
          </Button>
        ) : (
          <Button
            className="p-2 h-fit absolute bottom-2 right-2 bg-brand hover:bg-brand-dark text-white disabled:opacity-50 disabled:cursor-not-allowed transition-all cursor-pointer rounded-lg"
            onClick={(event) => {
              event.preventDefault();
              submitForm();
            }}
            disabled={input.length === 0}
          >
            <ArrowUpIcon size={16} />
          </Button>
        )}
      </div>
    </div>
  );
}
