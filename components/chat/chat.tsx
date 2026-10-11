"use client";

import { AssistantAvatar } from "./AssistantAvatar";
import { PreviewMessage, ThinkingMessage } from "./message";
import { MultimodalInput } from "./multimodal-input";
import { Overview } from "./overview";
import { ErrorBanner } from "./TurnExtras";
import type { LensUIMessage, StatusEvent } from "./types";
import { useScrollToBottom } from "@/hooks/use-scroll-to-bottom";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { Plus, X } from "lucide-react";
import { useState, useEffect, useMemo } from "react";
import type { SiteConfig } from "@/lib/site/schema";

/** Where this tab keeps its conversation, so a reload does not lose it. */
const SESSION_KEY = "lens-chat";

/**
 * How long a turn runs before the bubble says why and offers a way out. A
 * warm turn takes 5 to 15 seconds.
 */
const SLOW_AFTER_MS = 15_000;

/** The wait before the one automatic retry after a 429. */
const BUSY_RETRY_MS = 2_000;

type StoredChat = { id: string; messages: LensUIMessage[] };

function newChatId(): string {
  return crypto.randomUUID();
}

function loadChat(): StoredChat | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    const stored = raw ? JSON.parse(raw) : null;
    return stored && typeof stored.id === "string" && Array.isArray(stored.messages)
      ? stored
      : null;
  } catch {
    return null;
  }
}

function saveChat(chat: StoredChat): void {
  try {
    window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(chat));
  } catch {
    // Most likely over the storage quota: keep the conversation, drop the
    // rows behind its CSV buttons.
    try {
      const lean = chat.messages.map((m) => ({
        ...m,
        parts: m.parts.filter((p) => p.type !== "data-table"),
      }));
      window.sessionStorage.setItem(SESSION_KEY, JSON.stringify({ ...chat, messages: lean }));
    } catch {
      // Storage is unavailable (private mode, blocked): the chat still works.
    }
  }
}

/**
 * The messages as the backend gets them. A table part can hold thousands of
 * rows that would otherwise be sent back with every later question, so it
 * stays on this page. The small turn part goes: it carries the numbers the
 * turn's queries returned, which the backend checks later answers against.
 */
function withoutDataParts(messages: LensUIMessage[]): LensUIMessage[] {
  return messages.map((m) => ({
    ...m,
    parts: m.parts.filter((p) => !p.type.startsWith("data-") || p.type === "data-turn"),
  }));
}

export function Chat({ site, signIn = false }: { site: SiteConfig; signIn?: boolean }) {
  const [input, setInput] = useState("");
  const [isContextBannerVisible, setIsContextBannerVisible] = useState(true);
  const [session, setSession] = useState<StoredChat>(() => ({ id: newChatId(), messages: [] }));
  const [progress, setProgress] = useState<StatusEvent | null>(null);
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const [retryWhenStopped, setRetryWhenStopped] = useState(false);
  const [userName, setUserName] = useState<string | null>(null);

  // Who is signed in, when sign-in is on (lib/auth.ts). Off, the endpoint
  // fails or returns no user, and the header simply shows no name.
  useEffect(() => {
    fetch("/auth/session")
      .then((response) => (response.ok ? response.json() : null))
      .then((session) => setUserName(session?.user?.name ?? null))
      .catch(() => setUserName(null));
  }, []);

  // After mount, not during render: the server has no sessionStorage.
  useEffect(() => {
    const stored = loadChat();
    if (stored) setSession(stored);
  }, []);

  const transport = useMemo(
    () =>
      new DefaultChatTransport<LensUIMessage>({
        api: "/api/chat",
        prepareSendMessagesRequest: ({ id, messages, body }) => ({
          body: { ...body, id, messages: withoutDataParts(messages) },
        }),
        // One automatic retry after a 429, with the bubble saying so.
        fetch: async (url, init) => {
          const response = await fetch(url, init);
          if (response.status !== 429) return response;
          setBusy(true);
          await new Promise((resolve) => setTimeout(resolve, BUSY_RETRY_MS));
          setBusy(false);
          return fetch(url, init);
        },
      }),
    [],
  );

  const {
    messages,
    sendMessage,
    regenerate,
    status,
    stop,
    error,
    clearError,
  } = useChat<LensUIMessage>({
    // A new id is a new chat: "New chat" and a restored session both swap it.
    id: session.id,
    messages: session.messages,
    transport,
    onData: (part) => {
      if (part.type === "data-status") setProgress(part.data as StatusEvent);
    },
  });

  const isLoading = status === "streaming" || status === "submitted";

  // The progress line and the slow notice belong to one turn each.
  useEffect(() => {
    setProgress(null);
    setSlow(false);
    if (!isLoading) return;
    const timer = setTimeout(() => setSlow(true), SLOW_AFTER_MS);
    return () => clearTimeout(timer);
  }, [isLoading]);

  // "Stop and retry": the retry waits until the stop has landed.
  useEffect(() => {
    if (retryWhenStopped && !isLoading) {
      setRetryWhenStopped(false);
      clearError();
      regenerate();
    }
  }, [retryWhenStopped, isLoading, clearError, regenerate]);

  useEffect(() => {
    if (status === "ready" || status === "error") {
      saveChat({ id: session.id, messages });
    }
  }, [messages, status, session.id]);

  const lastMessage = messages[messages.length - 1];
  const lastTurnFailed =
    lastMessage?.role === "assistant" &&
    lastMessage.parts.some((p) => p.type === "data-turn" && p.data.status === "error");
  const showErrorBanner = !isLoading && (Boolean(error) || lastTurnFailed);

  const retry = () => {
    clearError();
    regenerate();
  };

  /** Stores a thumbs up or down on one answer; true once it is saved. */
  const sendFeedback = async (
    message: LensUIMessage,
    rating: "up" | "down",
    comment?: string,
  ): Promise<boolean> => {
    const textOf = (m: LensUIMessage | undefined) =>
      (m?.parts ?? []).map((p) => (p.type === "text" ? p.text : "")).join("");
    const index = messages.findIndex((m) => m.id === message.id);
    const question = messages
      .slice(0, index)
      .reverse()
      .find((m) => m.role === "user");
    try {
      const response = await fetch("/api/feedback", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          rating,
          comment: comment?.trim() || undefined,
          session_id: session.id,
          message_id: message.id,
          question: textOf(question),
          answer: textOf(message),
        }),
      });
      return response.ok;
    } catch {
      return false;
    }
  };

  const startNewChat = async () => {
    await stop();
    try {
      window.sessionStorage.removeItem(SESSION_KEY);
    } catch {
      // Nothing stored to remove.
    }
    setInput("");
    setIsContextBannerVisible(true);
    setSession({ id: newChatId(), messages: [] });
  };

  const handleSubmit = (e?: { preventDefault?: () => void }) => {
    e?.preventDefault?.();
    if (input.trim()) {
      clearError();
      sendMessage({ text: input });
      setInput("");
    }
  };

  const append = async (message: any): Promise<string | null | undefined> => {
    const text = message.content ?? message.text;
    if (text) {
      clearError();
      await sendMessage({ text });
    }
    return null;
  };

  const [messagesContainerRef, messagesEndRef] =
    useScrollToBottom<HTMLDivElement>();

  useEffect(() => {
    if (messagesEndRef.current && messages.length > 0) {
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "end"
        });
      });
    }
  }, [messages.length, showErrorBanner]);

  return (
    <div className="flex flex-col min-w-0 h-[calc(100dvh-64px)] bg-background">
      {(messages.length > 0 || userName || signIn) && (
        <div className="shrink-0 border-b border-border bg-surface px-4 py-2">
          <div className="max-w-3xl mx-auto flex items-center justify-end gap-3">
            {userName ? (
              <span className="text-xs text-muted-foreground truncate">
                {site.chat.messages.signedInAs.replace("{name}", userName)}
                {" · "}
                <a href="/auth/signout" className="font-semibold text-primary hover:underline">
                  {site.chat.messages.signOut}
                </a>
              </span>
            ) : (
              signIn && (
                <a
                  href="/auth/signin?callbackUrl=/chat"
                  className="text-xs font-semibold text-primary hover:underline truncate"
                >
                  {site.chat.messages.signIn}
                </a>
              )
            )}
            {messages.length > 0 && (
              <button
                type="button"
                onClick={startNewChat}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-semibold text-primary hover:bg-muted transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-light"
              >
                <Plus className="w-3.5 h-3.5" aria-hidden />
                {site.chat.messages.newChat}
              </button>
            )}
          </div>
        </div>
      )}

      <div
        ref={messagesContainerRef}
        className="flex flex-col min-w-0 gap-4 flex-1 overflow-y-scroll pt-4 px-4"
      >
        {/* Initial AI Assistant Welcome Display */}
        {messages.length === 0 && <Overview site={site} />}

        {/* Context Banner */}
        {messages.length > 0 && isContextBannerVisible && (
          <div className="max-w-3xl mx-auto w-full">
            <div className="bg-surface border border-border border-l-4 border-l-brand rounded-xl p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-3 flex-1">
                  <AssistantAvatar size="md" />
                  <div className="flex-1">
                    <h3 className="font-display text-sm font-extrabold text-primary">{site.chat.agent}</h3>
                    <p className="text-xs text-muted-foreground mt-1">
                      {site.chat.messages.welcomeMessage}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setIsContextBannerVisible(false)}
                  className="text-muted-foreground hover:text-primary transition-colors shrink-0 cursor-pointer rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-light"
                  aria-label="Close"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Messages */}
        {messages.map((message, index) => (
          <PreviewMessage
            key={message.id}
            site={site}
            message={message}
            chatId={session.id}
            append={append}
            // Not on the answer still streaming in.
            onFeedback={
              isLoading && index === messages.length - 1
                ? undefined
                : (rating, comment) => sendFeedback(message, rating, comment)
            }
          />
        ))}

        {isLoading &&
          messages.length > 0 &&
          messages[messages.length - 1].role === "user" && (
            <ThinkingMessage
              site={site}
              status={progress}
              busy={busy}
              slow={slow}
              onStopAndRetry={() => {
                setRetryWhenStopped(true);
                stop();
              }}
            />
          )}

        {showErrorBanner && <ErrorBanner site={site} onRetry={retry} />}

        {/* Auto-scroll anchor */}
        <div
          ref={messagesEndRef}
          className="shrink-0 min-w-[24px] min-h-[24px]"
        />
      </div>

      <div className="p-4 bg-surface border-t border-border">
        <form className="flex mx-auto gap-2 w-full md:max-w-3xl">
          <MultimodalInput
            site={site}
            input={input}
            setInput={setInput}
            handleSubmit={handleSubmit}
            isLoading={isLoading}
            stop={stop}
            messages={messages}
            append={append}
          />
        </form>
      </div>
    </div>
  );
}
