"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { A2ui } from "@/components/A2ui";
import { Button } from "@/design";
import { checkMessages, type A2uiMessage } from "@/lib/a2ui/check";

/** The first heading in a surface: what to call it while it is folded away. */
export function titleOf(messages: A2uiMessage[]): string {
  for (const m of messages) {
    const components = (m.updateComponents as { components?: { component?: string; variant?: string; text?: unknown }[] } | undefined)?.components ?? [];
    const heading = components.find((c) => c.component === "Text" && /^h[1-5]$/.test(c.variant ?? "") && typeof c.text === "string");
    if (heading) return String(heading.text);
  }
  return "Card";
}

/**
 * What a member's tool showed (a plan, an account), under their message. In a group it starts folded to its title: several members
 * may each show something, and the conversation should stay readable. A button on it speaks to its author, in the group's open.
 */
export function GroupCard({ ui, author, onSend }: { ui: unknown; author: string; onSend: (text: string) => void }) {
  const [open, setOpen] = useState(false);
  const messages = checkMessages(ui);
  if (!messages) return null;
  return (
    <div className="mt-1 space-y-1">
      <Button variant="secondary" size="sm" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown /> : <ChevronRight />}
        {titleOf(messages)}
      </Button>
      {open && <A2ui messages={messages} onSend={(text) => onSend(`@${author} ${text}`)} />}
    </div>
  );
}
