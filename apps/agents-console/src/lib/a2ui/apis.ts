/** The parts our chat catalog adds to A2UI's basic catalog: a key number, a status badge, a progress bar, a callout, initials. */
import { CommonSchemas } from "@a2ui/web_core/v0_9";
import { z } from "zod";

const common = { accessibility: CommonSchemas.AccessibilityAttributes.optional(), weight: z.number().optional() };
const Tone = z.enum(["success", "warning", "danger", "info"]);

export const MetricApi = {
  name: "Metric",
  schema: z
    .object({
      ...common,
      label: CommonSchemas.DynamicString,
      value: CommonSchemas.DynamicString,
      detail: CommonSchemas.DynamicString.optional(),
      trend: z.enum(["up", "down", "neutral"]).optional(),
    })
    .strict()
    .describe("A key number with a label, an optional detail line, and a trend colour (up green, down red). Put several in a Row."),
};

export const BadgeApi = {
  name: "Badge",
  schema: z
    .object({ ...common, text: CommonSchemas.DynamicString, tone: Tone.optional() })
    .strict()
    .describe("A short status label. tone colours it: success (green), warning (amber), danger (red), info (accent)."),
};

export const ProgressApi = {
  name: "Progress",
  schema: z
    .object({ ...common, value: CommonSchemas.DynamicNumber, max: z.number().optional(), label: CommonSchemas.DynamicString.optional(), tone: Tone.optional() })
    .strict()
    .describe("A bar showing value out of max (default 100). tone colours it."),
};

export const CalloutApi = {
  name: "Callout",
  schema: z
    .object({ ...common, title: CommonSchemas.DynamicString.optional(), text: CommonSchemas.DynamicString, tone: Tone.optional() })
    .strict()
    .describe("A highlighted note: a warning, a caveat, how something works. tone colours it (default info)."),
};

export const AvatarApi = {
  name: "Avatar",
  schema: z
    .object({ ...common, name: CommonSchemas.DynamicString, size: z.enum(["sm", "md", "lg"]).optional() })
    .strict()
    .describe("The initials of a name in a small square (a ticker, a person). Loads no picture."),
};

// The catalog without any React: what the prompt generator and the checks read, so they run on a server or in a script.
import { Catalog } from "@a2ui/web_core/v0_9";
import { basicCatalog } from "@a2ui/web_core/v0_9/basic_catalog";
import { CATALOG_ID } from "./check";

export const apiCatalog = new Catalog(
  CATALOG_ID,
  "v0.9",
  [...[...basicCatalog.components.values()].map((c) => ({ name: c.name, schema: c.schema })), MetricApi, BadgeApi, ProgressApi, CalloutApi, AvatarApi],
  [...basicCatalog.functions.values()].filter((f) => f.name !== "openUrl"),
);
