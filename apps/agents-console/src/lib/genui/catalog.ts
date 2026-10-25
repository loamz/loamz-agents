/**
 * What an agent or a tool may draw in a chat message, in the json-render standard (https://json-render.dev).
 *
 * The parts and their props are json-render's own shadcn definitions, so a model that knows that vocabulary writes them
 * correctly; three additions are marked below. `className` is removed from every part: a model styles with props, never with
 * classes. This file has no React in it, so the prompt generator (`pnpm genui:prompt`) can import it.
 */
import { defineCatalog } from "@json-render/core";
import { schema } from "@json-render/react/schema";
import { shadcnComponentDefinitions as standard } from "@json-render/shadcn/catalog";
import { z } from "zod";

const TONES = ["success", "warning", "danger", "info"] as const;

/** A standard part without its `className` prop. */
function plain<D extends { props: z.ZodObject<z.ZodRawShape> }>(def: D): D {
  return { ...def, props: def.props.omit({ className: true }) } as unknown as D;
}

export const catalog = defineCatalog(schema, {
  components: {
    Card: plain(standard.Card),
    Stack: plain(standard.Stack),
    Grid: plain(standard.Grid),
    Separator: standard.Separator,
    Heading: standard.Heading,
    Text: standard.Text,
    Alert: standard.Alert,
    Table: standard.Table,
    Button: standard.Button,
    Avatar: standard.Avatar,
    // Added: a tone, so a status can be green, amber or red (BUY, SELL, risk exit).
    Badge: {
      props: standard.Badge.props.extend({ tone: z.enum(TONES).nullable() }),
      description: "Status badge. tone colours it: success (green), warning (amber), danger (red), info (accent).",
    },
    // Added: a tone for the bar (a weight, a limit used).
    Progress: {
      props: standard.Progress.props.extend({ tone: z.enum(TONES).nullable() }),
      description: "Progress bar, value 0-100 (or 0-max). tone colours the bar.",
    },
    // Added: json-render's documented KPI example.
    Metric: {
      props: z.object({
        label: z.string(),
        value: z.string(),
        detail: z.string().nullable(),
        trend: z.enum(["up", "down", "neutral"]).nullable(),
      }),
      description: "A key number with a label, an optional detail line, and a trend colour (up green, down red). Use in a Grid.",
    },
  },
  actions: {
    // Added: the only way a card talks to the agent; the agent's own tools and approvals still apply to whatever it asks for.
    send: {
      params: z.object({ text: z.string() }),
      description: "Send this text to the agent as if the person had typed it, e.g. a Button that sends \"Approve plan 3f2a9c1e\".",
    },
  },
});
