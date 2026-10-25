"use client";

import dynamic from "next/dynamic";
import type { A2uiBlockProps } from "./A2uiBlock";

const Loading = () => <div className="my-1 h-16 w-full animate-pulse rounded-xl border border-border bg-card" aria-label="Drawing" />;

/** The A2UI renderer needs the browser (custom elements), so it is loaded after the page, never on the server. */
const Block = dynamic(() => import("./A2uiBlock"), { ssr: false, loading: Loading });

export function A2ui(props: A2uiBlockProps) {
  return <Block {...props} />;
}
