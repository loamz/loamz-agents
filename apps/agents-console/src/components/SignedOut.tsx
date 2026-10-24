"use client";

import { ArrowRight, Database, Lock, ScrollText, Users } from "lucide-react";
import type { CSSProperties } from "react";
import { Button } from "@/design/ui/Button";
import { LoamzMark } from "@/components/LoamzMark";

/** What a person sees before signing in: what the product does, a picture of it working, and one way forward. */
export function SignedOut({ onSignIn, notice }: { onSignIn: () => void; notice?: string | null }) {
  const step = (n: number) => ({ "--stagger": n }) as CSSProperties;

  return (
    <div className="signed-out relative isolate flex min-h-dvh flex-col overflow-hidden bg-background text-foreground">
      <div aria-hidden className="signed-out-backdrop absolute inset-0 -z-10" />

      <header className="loamz-fade-up mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-5">
        <div className="flex items-center gap-2.5">
          <LoamzMark className="icon-free h-8 w-8" />
          <span className="text-xl font-semibold tracking-tight">Loamz</span>
        </div>
        <Button variant="secondary" size="lg" onClick={onSignIn}>
          Sign in
        </Button>
      </header>

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col items-center px-6 pb-16 pt-10 text-center md:pt-14">
        <span
          className="loamz-fade-up inline-flex items-center gap-2 rounded-full border border-border bg-card/70 px-3 py-1 text-xs text-muted backdrop-blur"
          style={step(1)}
        >
          <span className="size-1.5 rounded-full bg-accent-2" />
          Your data and your agents, in one place
        </span>

        <h1 className="signed-out-title loamz-fade-up mt-6 max-w-3xl" style={step(2)}>
          Ask your data.
          <br />
          <span className="signed-out-gradient">Let agents do the work.</span>
        </h1>

        <p className="loamz-fade-up mt-5 max-w-xl text-pretty text-base text-muted" style={step(3)}>
          Ask questions in plain language and get answers from the data your team is allowed to see. Every step an agent takes is
          recorded.
        </p>

        <div className="loamz-fade-up mt-8 flex flex-col items-center gap-3" style={step(4)}>
          <Button variant="accent" size="lg" className="signed-out-cta" onClick={onSignIn}>
            Continue to sign in <ArrowRight />
          </Button>
          <p className="flex items-center gap-1.5 text-xs text-muted">
            <Lock /> Your conversations are private and secure
          </p>
        </div>

        {notice && (
          <div
            role="status"
            className="loamz-fade-up mt-6 max-w-md rounded-xl border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning"
            style={step(5)}
          >
            {notice}
          </div>
        )}

        <Preview step={step(5)} />

        <ul className="loamz-fade-up mt-14 grid w-full max-w-4xl gap-4 text-left sm:grid-cols-3" style={step(6)}>
          <Point icon={<Database />} title="Answers where the data lives">
            Questions run on the data engine, so only the answer comes back, not the whole table.
          </Point>
          <Point icon={<Users />} title="Access follows your teams">
            Sales sees sales data and marketing sees marketing data, for people and for their agents.
          </Point>
          <Point icon={<ScrollText />} title="Every action is recorded">
            What an agent read and did is kept in a record that can be checked later.
          </Point>
        </ul>
      </main>
    </div>
  );
}

function Point({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <li className="rounded-xl border border-border bg-card/60 p-4 backdrop-blur">
      <span className="mb-3 flex size-control-lg items-center justify-center rounded-lg bg-accent/10 text-accent">{icon}</span>
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="mt-1 text-sm text-muted">{children}</p>
    </li>
  );
}

const EXAMPLE: { label: string; share: number }[] = [
  { label: "Manhattan", share: 100 },
  { label: "Queens", share: 82 },
  { label: "Brooklyn", share: 61 },
  { label: "Bronx", share: 44 },
];

/** A picture of a conversation, not live data: the bars only show the shape of an answer. */
function Preview({ step }: { step: CSSProperties }) {
  return (
    <figure
      className="signed-out-preview loamz-fade-up relative mt-14 w-full max-w-3xl overflow-hidden rounded-2xl border border-border bg-card text-left"
      style={step}
    >
      <div className="flex items-center gap-1.5 border-b border-border px-4 py-3">
        <span className="size-2.5 rounded-full bg-border-hover" />
        <span className="size-2.5 rounded-full bg-border-hover" />
        <span className="size-2.5 rounded-full bg-border-hover" />
        <figcaption className="ml-3 text-xs text-muted">Example conversation</figcaption>
      </div>

      <div className="space-y-4 p-5">
        <div className="flex justify-end">
          <p className="max-w-sm rounded-2xl rounded-br-md bg-bubble px-4 py-2.5 text-sm">
            Which borough has the highest average taxi fare?
          </p>
        </div>

        <div className="flex gap-3">
          <span className="mt-0.5 flex size-control-lg shrink-0 items-center justify-center rounded-full bg-accent/10">
            <LoamzMark className="icon-free h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1 space-y-3">
            <p className="inline-flex items-center gap-2 rounded-md bg-step px-2.5 py-1 font-mono text-xs text-muted">
              <Database /> Ran on the data engine
            </p>
            <p className="text-sm">Here is the average fare by borough, highest first.</p>
            <div className="space-y-2">
              {EXAMPLE.map((row) => (
                <div key={row.label} className="grid grid-cols-[5.5rem_1fr] items-center gap-3 text-xs">
                  <span className="text-muted">{row.label}</span>
                  <span className="h-2 overflow-hidden rounded-full bg-border">
                    <span className="signed-out-bar block h-full rounded-full" style={{ width: `${row.share}%` }} />
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </figure>
  );
}
