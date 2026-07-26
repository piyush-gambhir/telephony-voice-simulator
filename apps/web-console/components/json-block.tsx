"use client";

import * as React from "react";
import { Check, Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Tokenize a pretty-printed JSON string into color-coded spans. Content is
// fully HTML-escaped first, so injecting the resulting markup is safe.
function highlight(json: string): string {
  return escapeHtml(json).replace(
    /("(?:\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*")(\s*:)?|\b(true|false)\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,
    (match, str, colon, bool) => {
      if (str !== undefined) {
        if (colon) {
          return `<span class="tok-key">${str}</span><span class="tok-punc">${colon}</span>`;
        }
        return `<span class="tok-str">${str}</span>`;
      }
      if (bool !== undefined) return `<span class="tok-bool">${match}</span>`;
      if (match === "null") return `<span class="tok-null">null</span>`;
      return `<span class="tok-num">${match}</span>`;
    }
  );
}

function toPretty(value: unknown): string {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

function useCopy() {
  const [copied, setCopied] = React.useState(false);
  const copy = React.useCallback((text: string) => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  }, []);
  return { copied, copy };
}

/** Block JSON: color-coded, pretty-printed, with a copy button. */
export function JsonBlock({
  value,
  className,
}: {
  value: unknown;
  className?: string;
}) {
  const pretty = React.useMemo(() => toPretty(value), [value]);
  const { copied, copy } = useCopy();
  return (
    <div className={cn("group/json relative rounded-xl bg-muted/60", className)}>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={() => copy(pretty)}
        aria-label="Copy JSON"
        className="absolute right-2 top-2 z-10 h-7 w-7 bg-background/70 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover/json:opacity-100"
      >
        {copied ? (
          <Check className="h-3.5 w-3.5 text-success" />
        ) : (
          <Copy className="h-3.5 w-3.5" />
        )}
      </Button>
      <pre className="no-scrollbar overflow-x-auto p-3 pr-10 text-xs leading-relaxed">
        <code dangerouslySetInnerHTML={{ __html: highlight(pretty) }} />
      </pre>
    </div>
  );
}

/** Inline JSON: single-line color-coded, click-to-copy. */
export function JsonInline({ value }: { value: unknown }) {
  const compact = React.useMemo(() => {
    if (typeof value === "string") {
      try {
        return JSON.stringify(JSON.parse(value));
      } catch {
        return value;
      }
    }
    return JSON.stringify(value);
  }, [value]);
  const { copied, copy } = useCopy();
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      onClick={() => copy(compact)}
      title="Click to copy"
      className="h-auto max-w-full gap-1 bg-muted px-1.5 py-0.5 text-right font-mono text-xs"
    >
      <code
        className="truncate"
        dangerouslySetInnerHTML={{ __html: highlight(compact) }}
      />
      {copied ? (
        <Check className="h-3 w-3 shrink-0 text-success" />
      ) : (
        <Copy className="h-3 w-3 shrink-0 text-muted-foreground/60" />
      )}
    </Button>
  );
}
