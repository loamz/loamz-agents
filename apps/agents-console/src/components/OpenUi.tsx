"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import type { OpenUiBlockProps } from "./OpenUiBlock";

const Loading = () => <div className="my-1 h-24 w-full animate-pulse rounded-xl border border-border bg-card" aria-label="Drawing" />;

/** The OpenUI library is large and needs the browser, so it is loaded after the page, and only when an answer uses it. */
const Block = dynamic(() => import("./OpenUiBlock"), { ssr: false, loading: Loading });

/** An ```openui fence in a reply: drawn as it streams; if it never becomes anything drawable, the raw text is shown. */
export function OpenUiFence({ source, streaming = false, onSend, threadId }: Omit<OpenUiBlockProps, "onEmpty">) {
  const [empty, setEmpty] = useState(false);
  if (!source.trim()) return streaming ? <Loading /> : null;
  return (
    <>
      <Block source={source} streaming={streaming} onSend={onSend} threadId={threadId} onEmpty={setEmpty} />
      {empty && !streaming && (
        <pre className="my-1 max-h-48 overflow-auto rounded-lg bg-code p-3 text-xs text-code-foreground" title="This could not be drawn">
          {source}
        </pre>
      )}
    </>
  );
}
