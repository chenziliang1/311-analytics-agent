'use client';

import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { citationId, linkCitations } from '@/lib/chat/citations';
import { resultRef } from '@/lib/chat/results';
import type { SiteConfig } from '@/lib/site/schema';

import { getBlockRenderer } from './blocks';
import { Citation } from './Citation';
import { ResultTable } from './ResultTable';
import type { TableData } from './types';

interface MarkdownProps {
  children: string;
  onQuestionClick?: (question: string) => void;
  /** Present on an answer: lets a `[KB:...]` citation open its document. */
  site?: SiteConfig;
  chatId?: string;
  /** The answer's query results, drawn where it writes `[RESULT:<id>]`. */
  tables?: TableData[];
}

/** The text of a paragraph that holds nothing but text, else null. */
function onlyText(children: React.ReactNode): string | null {
  const nodes = Array.isArray(children) ? children : [children];
  if (nodes.length !== 1 || typeof nodes[0] !== 'string') return null;
  return nodes[0];
}

export const Markdown = ({ children, onQuestionClick, site, chatId, tables }: MarkdownProps) => {
  // Preprocess markdown to handle #ask: links with spaces, and to turn the
  // agent's citation markers into links the renderer below can recognise.
  const preprocessedChildren = linkCitations(
    children.replace(/\[([^\]]+)\]\(#ask:([^)]+)\)/g, '[$1](<#ask:$2>)'),
  );

  return (
    <div className="prose prose-sm max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => {
            // A paragraph that is one `[RESULT:<id>]` is that result's table.
            const text = onlyText(children);
            const id = text === null ? null : resultRef(text);
            if (id) {
              const table = tables?.find((t) => t.result_id === id);
              if (table) {
                return <ResultTable table={table} rowsShown={site?.chat.messages.tableRowsShown ?? ''} />;
              }
              // The rows have not arrived (still streaming) or never will
              // (an id the turn did not return): the reference, quietly.
              return <p className="mb-3 last:mb-0 text-xs text-muted-foreground">{text}</p>;
            }
            return <p className="mb-3 last:mb-0 leading-relaxed">{children}</p>;
          },
          a: ({ href, children }) => {
            // A citation: the chip fetches and shows the cited document.
            const cited = citationId(href);
            if (cited) {
              return site ? <Citation id={cited} chatId={chatId} site={site} /> : <code>{cited}</code>;
            }
            // Handle clickable question links
            if (href?.startsWith('#ask:')) {
              const question = decodeURIComponent(href.replace('#ask:', ''));
              return (
                <button
                  onClick={() => onQuestionClick?.(question)}
                  className="text-brand hover:text-brand-dark underline font-medium cursor-pointer transition-colors"
                >
                  {children}
                </button>
              );
            }
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-brand hover:text-brand-dark underline"
              >
                {children}
              </a>
            );
          },
          // The one dispatch point for fenced blocks. Inline code has no
          // className; a fenced block carries `language-*`, and that language
          // selects a renderer from blocks/ (this is where Mermaid enters).
          code: ({ className, children }) => {
            if (!className) {
              return (
                <code className="bg-muted border border-border px-1.5 py-0.5 rounded text-[0.85em] font-mono text-primary">
                  {children}
                </code>
              );
            }
            const language = /language-(\w+)/.exec(className)?.[1] ?? '';
            const source = String(children).replace(/\n$/, '');
            const Block = getBlockRenderer(language);
            return <Block source={source} language={language} />;
          },
          // Fenced blocks render their own outer container (see
          // blocks/CodeBlock.tsx), so pre only passes through. Wrapping again
          // would nest <pre> inside <pre>, or a <div> inside a <pre>.
          pre: ({ children }) => <>{children}</>,
          ul: ({ children }) => <ul className="list-disc pl-6 mb-3 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal pl-6 mb-3 space-y-1">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          strong: ({ children }) => <strong className="font-bold text-brand">{children}</strong>,
          em: ({ children }) => <em className="italic text-primary/70">{children}</em>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-4 border-accent pl-4 my-3 text-primary/80 bg-accent/10 py-2 rounded-r">
              {children}
            </blockquote>
          ),
          h1: ({ children }) => <h1 className="font-display text-2xl font-extrabold mb-3 mt-4">{children}</h1>,
          h2: ({ children }) => <h2 className="font-display text-xl font-extrabold mb-3 mt-4">{children}</h2>,
          h3: ({ children }) => <h3 className="font-display text-lg font-bold mb-2 mt-3">{children}</h3>,
          h4: ({ children }) => <h4 className="font-display text-base font-bold mb-2 mt-3">{children}</h4>,
          table: ({ children }) => (
            <div className="overflow-x-auto my-3 rounded-lg border border-border">
              <table className="min-w-full border-collapse">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-brand text-white">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="px-3 py-2 text-left font-display text-xs font-bold uppercase tracking-[0.04em] whitespace-nowrap">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="px-3 py-2 text-sm border-t border-border tabular-nums whitespace-nowrap">
              {children}
            </td>
          ),
        }}
      >
        {preprocessedChildren}
      </ReactMarkdown>
    </div>
  );
};
