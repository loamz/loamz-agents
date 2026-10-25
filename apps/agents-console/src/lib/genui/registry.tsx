"use client";

/** How each json-render part is drawn: with the console's design primitives and tokens, so a card follows the theme. */
import { defineRegistry } from "@json-render/react";
import { Badge, Button, cn } from "@/design";
import { catalog } from "./catalog";

type Tone = "success" | "warning" | "danger" | "info";

const GAP = { none: "gap-0", sm: "gap-1.5", md: "gap-3", lg: "gap-4", xl: "gap-6" } as const;
const ALIGN = { start: "items-start", center: "items-center", end: "items-end", stretch: "items-stretch" } as const;
const JUSTIFY = { start: "justify-start", center: "justify-center", end: "justify-end", between: "justify-between", around: "justify-around" } as const;
const FILL: Record<Tone, string> = { success: "bg-success", warning: "bg-warning", danger: "bg-danger", info: "bg-accent" };
const TEXT: Record<Tone, string> = { success: "text-success", warning: "text-warning", danger: "text-danger", info: "text-accent" };
const BADGE_TONE = { success: "success", warning: "warning", danger: "danger", info: "accent" } as const;
const ALERT_TONE = { info: "info", success: "success", warning: "warning", error: "danger" } as const;
const WASH: Record<Tone, string> = {
  success: "border-success/40 bg-success/10",
  warning: "border-warning/40 bg-warning/10",
  danger: "border-danger/40 bg-danger/10",
  info: "border-accent/40 bg-accent/10",
};
const MAX = { sm: "max-w-sm", md: "max-w-md", lg: "max-w-2xl", full: "max-w-full" } as const;

const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");

export const { registry } = defineRegistry(catalog, {
  components: {
    Card: ({ props, children }) => (
      <section className={cn("w-full space-y-3 rounded-xl border border-border bg-surface p-3", props.maxWidth && MAX[props.maxWidth], props.centered && "mx-auto")}>
        {(props.title || props.description) && (
          <header>
            {props.title && <h3 className="text-sm font-semibold text-foreground">{props.title}</h3>}
            {props.description && <p className="text-xs text-muted">{props.description}</p>}
          </header>
        )}
        {children}
      </section>
    ),
    Stack: ({ props, children }) => (
      <div
        className={cn(
          "flex min-w-0",
          props.direction === "horizontal" ? "flex-row flex-wrap" : "flex-col",
          GAP[props.gap ?? "md"],
          props.align && ALIGN[props.align],
          props.justify && JUSTIFY[props.justify],
        )}
      >
        {children}
      </div>
    ),
    // Columns collapse on a narrow bubble: each cell is at least 9rem wide, never more than `columns` across.
    Grid: ({ props, children }) => (
      <div
        className={cn("grid", GAP[props.gap ?? "md"])}
        style={{ gridTemplateColumns: `repeat(auto-fit, minmax(max(9rem, calc(100% / ${Math.min(Math.max(props.columns ?? 2, 1), 6)} - 1rem)), 1fr))` }}
      >
        {children}
      </div>
    ),
    Separator: ({ props }) =>
      props.orientation === "vertical" ? <div className="w-px self-stretch bg-border" /> : <hr className="border-border" />,
    Heading: ({ props }) => {
      const size = { h1: "text-xl", h2: "text-lg", h3: "text-base", h4: "text-sm" }[props.level ?? "h3"];
      return <div role="heading" aria-level={Number((props.level ?? "h3")[1])} className={cn("font-semibold text-foreground", size)}>{props.text}</div>;
    },
    Text: ({ props }) => (
      <p
        className={cn(
          "text-sm leading-relaxed",
          props.variant === "caption" && "text-xs text-muted",
          props.variant === "muted" && "text-muted",
          props.variant === "lead" && "text-base text-foreground",
          props.variant === "code" && "rounded bg-code px-1.5 py-0.5 font-mono text-xs text-code-foreground",
          (!props.variant || props.variant === "body") && "text-foreground/90",
        )}
      >
        {props.text}
      </p>
    ),
    Badge: ({ props }) => (
      <Badge tone={props.tone ? BADGE_TONE[props.tone] : props.variant === "destructive" ? "danger" : "neutral"}>{props.text}</Badge>
    ),
    Alert: ({ props }) => {
      const tone = ALERT_TONE[props.type ?? "info"];
      return (
        <div role="note" className={cn("rounded-lg border p-3", WASH[tone])}>
          <div className={cn("text-sm font-semibold", TEXT[tone])}>{props.title}</div>
          {props.message && <p className="mt-0.5 text-xs leading-relaxed text-foreground/90">{props.message}</p>}
        </div>
      );
    },
    Table: ({ props }) => (
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-xs">
          {props.caption && <caption className="p-2 text-left text-muted">{props.caption}</caption>}
          <thead className="bg-card text-muted">
            <tr>
              {props.columns.map((c, i) => (
                <th key={i} className="whitespace-nowrap px-2.5 py-1.5 text-left font-medium">{c}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {props.rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} className="px-2.5 py-1.5 tabular-nums">{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ),
    Progress: ({ props }) => {
      const max = props.max && props.max > 0 ? props.max : 100;
      const pct = Math.min(100, Math.max(0, (props.value / max) * 100));
      return (
        <div className="space-y-1">
          {props.label && (
            <div className="flex justify-between text-xs">
              <span className="text-foreground">{props.label}</span>
              <span className="tabular-nums text-muted">{Math.round(pct)}%</span>
            </div>
          )}
          <div role="progressbar" aria-label={props.label ?? "progress"} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} className="h-1.5 overflow-hidden rounded-full bg-border">
            <div className={cn("h-full rounded-full", FILL[props.tone ?? "info"])} style={{ width: `${pct}%` }} />
          </div>
        </div>
      );
    },
    Metric: ({ props }) => (
      <div className="min-w-0 rounded-lg border border-border bg-card p-2.5">
        <div className="truncate text-2xs text-muted">{props.label}</div>
        <div className={cn("truncate text-xl font-semibold tabular-nums", props.trend === "up" && "text-success", props.trend === "down" && "text-danger", !props.trend || props.trend === "neutral" ? "text-foreground" : "")}>
          {props.value}
        </div>
        {props.detail && <div className="truncate text-2xs text-muted">{props.detail}</div>}
      </div>
    ),
    // Initials only: an avatar never loads an image from somewhere else.
    Avatar: ({ props }) => (
      <span
        title={props.name}
        className={cn(
          "inline-flex shrink-0 items-center justify-center rounded-md bg-badge font-bold text-badge-foreground",
          props.size === "lg" ? "size-10 text-xs" : props.size === "sm" ? "size-6 text-2xs" : "size-8 text-2xs",
        )}
      >
        {initials(props.name)}
      </span>
    ),
    Button: ({ props, emit }) => (
      <Button size="lg" variant={props.variant === "primary" ? "primary" : props.variant === "danger" ? "danger" : "secondary"} disabled={props.disabled ?? false} onClick={() => emit("press")}>
        {props.label}
      </Button>
    ),
  },
  actions: {
    // Handled by GenUi, which knows how to reach the chat; see components/GenUi.tsx.
    send: async () => {},
  },
});
