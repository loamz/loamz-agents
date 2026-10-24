"use client";

import { ArrowRight, Database, LogOut, MessageSquare } from "lucide-react";
import type { ReactNode } from "react";
import { LoamzMark } from "@/components/LoamzMark";
import { Button } from "@/design/ui/Button";

/** Where a signed-in person lands: one place to choose what to do, before the product itself. */
export function ProductHome({
  name,
  dataUrl,
  onSignOut,
}: {
  name: string;
  dataUrl: string | null;
  onSignOut: () => void;
}) {
  return (
    <div className="signed-out relative isolate flex min-h-dvh flex-col overflow-hidden bg-background text-foreground">
      <div aria-hidden className="signed-out-backdrop absolute inset-0 -z-10" />
      <header className="mx-auto flex w-full max-w-5xl items-center justify-between px-6 py-5">
        <div className="flex items-center gap-2.5">
          <LoamzMark className="icon-free h-8 w-8" />
          <span className="text-xl font-semibold tracking-tight">Loamz</span>
        </div>
        <Button variant="ghost" size="lg" onClick={onSignOut}>
          <LogOut /> Sign out
        </Button>
      </header>

      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col justify-center px-6 pb-24">
        <h1 className="loamz-fade-up text-3xl font-semibold tracking-tight">Welcome back, {name}</h1>
        <p className="loamz-fade-up mt-2 text-base text-muted" style={{ "--stagger": 1 } as React.CSSProperties}>
          What would you like to do?
        </p>

        <div className="mt-8 grid gap-4 sm:grid-cols-2">
          <Tile
            href="/chat"
            icon={<MessageSquare />}
            title="Chat"
            stagger={2}
            action="Open chat"
          >
            Talk to your assistants and agents, and ask questions of your data in plain language.
          </Tile>
          {dataUrl && (
            <Tile href={dataUrl} icon={<Database />} title="Data" stagger={3} action="Open data">
              Browse your tables and files, run queries, and manage who can see what.
            </Tile>
          )}
        </div>
      </main>
    </div>
  );
}

function Tile({
  href,
  icon,
  title,
  action,
  stagger,
  children,
}: {
  href: string;
  icon: ReactNode;
  title: string;
  action: string;
  stagger: number;
  children: ReactNode;
}) {
  return (
    <a
      href={href}
      className="loamz-fade-up group flex flex-col rounded-2xl border border-border bg-card/70 p-6 backdrop-blur transition-colors hover:border-accent hover:bg-card"
      style={{ "--stagger": stagger } as React.CSSProperties}
    >
      <span className="flex size-control-lg items-center justify-center rounded-lg bg-accent/10 text-accent">{icon}</span>
      <h2 className="mt-5 text-xl font-semibold">{title}</h2>
      <p className="mt-1.5 flex-1 text-sm text-muted">{children}</p>
      <span className="mt-6 inline-flex items-center gap-1.5 text-sm font-medium text-accent">
        {action} <ArrowRight className="transition-transform group-hover:translate-x-0.5" />
      </span>
    </a>
  );
}
