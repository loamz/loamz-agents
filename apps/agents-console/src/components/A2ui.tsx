"use client";

import dynamic from "next/dynamic";
import type { A2uiBlockProps } from "./A2uiBlock";
import { messagesFromFence } from "@/lib/a2ui/check";

const Loading = () => <div className="my-1 h-16 w-full animate-pulse rounded-xl border border-border bg-card" aria-label="Drawing" />;

/** The A2UI renderer needs the browser (custom elements), so it is loaded after the page, never on the server. */
const Block = dynamic(() => import("./A2uiBlock"), { ssr: false, loading: Loading });

export function A2ui(props: A2uiBlockProps) {
  return <Block {...props} />;
}

/** An ```a2ui fence in a model's reply: drawn as it streams; if it never becomes anything drawable, the raw text is shown. */
export function A2uiFence({ source, streaming, onSend, threadId }: { source: string; streaming: boolean; onSend?: (text: string) => void; threadId?: string | null }) {
  const messages = messagesFromFence(source);
  if (messages) return <A2ui messages={messages} onSend={onSend} threadId={threadId} streaming={streaming} />;
  if (streaming) return <Loading />;
  return (
    <pre className="my-1 max-h-48 overflow-auto rounded-lg bg-code p-3 text-xs text-code-foreground" title="This UI could not be drawn">
      {source}
    </pre>
  );
}
