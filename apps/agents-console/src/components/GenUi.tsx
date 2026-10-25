"use client";

import type { Spec } from "@json-render/core";
import { JSONUIProvider, Renderer } from "@json-render/react";
import { registry } from "@/lib/genui/registry";
import { specFromFence } from "@/lib/genui/spec";

/**
 * A json-render spec (https://json-render.dev) drawn inside a message. The parts come from lib/genui/catalog.ts; the only thing
 * a card can do is `send`: put text in the chat as if the person had typed it (so the agent's own approvals still apply).
 */
export function GenUi({ spec, onSend, loading = false }: { spec: Spec; onSend?: (text: string) => void; loading?: boolean }) {
  return (
    <div data-genui className="my-1 w-full min-w-0">
      <JSONUIProvider
        registry={registry}
        initialState={spec.state ?? {}}
        handlers={{
          send: (params) => {
            const text = typeof params?.text === "string" ? params.text.trim() : "";
            if (text) onSend?.(text);
          },
        }}
      >
        <Renderer spec={spec} registry={registry} loading={loading} />
      </JSONUIProvider>
    </div>
  );
}

/** A ```spec fence in a model's reply: drawn as it streams; if it never becomes a valid spec, the raw text is shown instead. */
export function SpecBlock({ source, streaming, onSend }: { source: string; streaming: boolean; onSend?: (text: string) => void }) {
  const spec = specFromFence(source);
  if (spec) return <GenUi spec={spec} onSend={onSend} loading={streaming} />;
  if (streaming) return <div className="my-1 h-16 w-full animate-pulse rounded-xl border border-border bg-card" aria-label="Drawing" />;
  return (
    <pre className="my-1 max-h-48 overflow-auto rounded-lg bg-code p-3 text-xs text-code-foreground" title="This UI could not be drawn">
      {source}
    </pre>
  );
}
