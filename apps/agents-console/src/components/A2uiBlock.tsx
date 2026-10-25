"use client";

/** A2UI surfaces drawn inside a message. Client only: the renderer needs the browser (see ./A2ui.tsx, which loads this lazily). */
import { A2uiSurface } from "@a2ui/react/v0_9";
import { MessageProcessor } from "@a2ui/web_core/v0_9";
import { Component, useEffect, useMemo, type ReactNode } from "react";
import { A2uiEnv } from "@/lib/a2ui/components";
import { chatCatalog, standardCatalog } from "@/lib/a2ui/catalog";
import { actionToMessage, type A2uiMessage } from "@/lib/a2ui/check";

class Contained extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

export interface A2uiBlockProps {
  messages: A2uiMessage[];
  onSend?: (text: string) => void;
  threadId?: string | null;
  /** The reply is still arriving: more messages may follow. */
  streaming?: boolean;
}

export default function A2uiBlock({ messages, onSend, threadId }: A2uiBlockProps) {
  const key = JSON.stringify(messages);
  const processor = useMemo(() => {
    const p = new MessageProcessor([chatCatalog, standardCatalog] as never[], (action) => {
      onSend?.(actionToMessage(action as { name: string; context?: Record<string, unknown> }));
    });
    for (const message of messages) {
      try {
        p.processMessages([message] as never); // one at a time: a message that does not fit costs itself, not the rest
      } catch {
        // dropped
      }
    }
    return p;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` is the content of `messages`; onSend is read when an action fires
  }, [key]);
  useEffect(() => () => processor.dispose(), [processor]);

  const surfaces = [...processor.getSurfaces().values()];
  if (!surfaces.length) return null;
  return (
    <A2uiEnv.Provider value={{ threadId }}>
      <div data-a2ui className="my-1 w-full min-w-0 space-y-2">
        {surfaces.map((surface) => (
          <Contained key={surface.id}>
            <A2uiSurface surface={surface as never} />
          </Contained>
        ))}
      </div>
    </A2uiEnv.Provider>
  );
}
