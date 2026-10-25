"use client";

/** An answer written in OpenUI Lang, drawn with OpenUI's component library. Client only (see ./OpenUi.tsx, which loads this lazily). */
import "@openuidev/react-ui/layered/styles/index.css";
import { Renderer } from "@openuidev/react-lang";
import { useEffect, useState } from "react";
import { OpenUiEnv, richLibrary } from "@/lib/openui/library";

export interface OpenUiBlockProps {
  source: string;
  /** The reply is still arriving: the answer is drawn as far as it has come. */
  streaming?: boolean;
  /** What a button's `@ToAssistant("...")` says: sent to the agent as if the person had typed it. */
  onSend?: (text: string) => void;
  threadId?: string | null;
  /** Called when the text could not be drawn at all. */
  onEmpty?: (empty: boolean) => void;
}

const isDark = () => document.documentElement.classList.contains("dark") || document.documentElement.dataset.theme === "dark";

/** Follows the console's light/dark switch, which OpenUI reads as an attribute on an ancestor. */
function useThemeName(): "light" | "dark" {
  const [name, setName] = useState<"light" | "dark">("dark");
  useEffect(() => {
    const update = () => setName(isDark() ? "dark" : "light");
    update();
    const watcher = new MutationObserver(update);
    watcher.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme"] });
    return () => watcher.disconnect();
  }, []);
  return name;
}

type ActionEvent = { type?: string; params?: Record<string, unknown>; humanFriendlyMessage?: string };

/** A click on an answer: a follow-up goes to the agent as a message; a link opens only if it is https; anything else is named. */
export function actionToSend(event: ActionEvent, open: (url: string) => void): string | null {
  const params = event.params ?? {};
  if (event.type === "open_url") {
    const url = typeof params.url === "string" ? params.url : "";
    if (/^https:\/\//i.test(url)) open(url);
    return null;
  }
  const said = (typeof params.context === "string" && params.context.trim()) || event.humanFriendlyMessage?.trim();
  if (event.type === "continue_conversation") return said || null;
  return said ? `[card action] ${said}` : null;
}

export default function OpenUiBlock({ source, streaming = false, onSend, threadId, onEmpty }: OpenUiBlockProps) {
  const theme = useThemeName();
  return (
    <OpenUiEnv.Provider value={{ threadId }}>
      <div data-openui data-openui-theme={theme} className="openui-host my-1 w-full min-w-0">
        <Renderer
          response={source}
          library={richLibrary}
          isStreaming={streaming}
          onParseResult={(result) => onEmpty?.(!result)}
          onAction={(event) => {
            const text = actionToSend(event as ActionEvent, (url) => window.open(url, "_blank", "noopener,noreferrer"));
            if (text) onSend?.(text);
          }}
        />
      </div>
    </OpenUiEnv.Provider>
  );
}
