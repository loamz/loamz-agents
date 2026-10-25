import ReactMarkdown from "react-markdown";
import { OpenUiFence } from "@/components/OpenUi";
import { separateFence } from "@/lib/a2ui/check";

/** A fenced block in a member's reply. A stable component, so a card drawn from a fence is not rebuilt each time the message updates. */
function GroupPre({ children: c }: { children?: React.ReactNode }) {
  // A member's reply may carry a rich page in an openui fence (drawn as in a one-to-one chat; a button on it cannot act here).
  const child = Array.isArray(c) ? c[0] : c;
  const props = (child as { props?: { className?: string; children?: unknown } } | undefined)?.props;
  if (/language-openui\b/.test(props?.className ?? "")) return <OpenUiFence source={String(props?.children ?? "").replace(/\n$/, "")} />;
  return <pre className="scroll-area overflow-x-auto rounded-lg bg-code p-3 text-xs text-code-foreground [&_code]:bg-transparent [&_code]:p-0">{c}</pre>;
}

/** An agent's message: its formatting (lists, bold, code, links) shown, at chat-bubble size. */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="space-y-2 text-sm wrap-break-word [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5">
      <ReactMarkdown
        components={{
          p: ({ children: c }) => <p className="whitespace-pre-wrap">{c}</p>,
          a: ({ children: c, href }) => (
            <a href={href} target="_blank" rel="noreferrer" className="text-accent underline underline-offset-2">
              {c}
            </a>
          ),
          code: ({ children: c }) => <code className="rounded bg-badge px-1 py-0.5 text-xs">{c}</code>,
          pre: GroupPre,
        }}
      >
        {separateFence(children)}
      </ReactMarkdown>
    </div>
  );
}
