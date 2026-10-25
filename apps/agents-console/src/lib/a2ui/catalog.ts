import { Catalog } from "@a2ui/web_core/v0_9";
import { basicCatalog } from "@a2ui/web_core/v0_9/basic_catalog";
import { implementations } from "./components";
import { BASIC_CATALOG_ID, CATALOG_ID } from "./check";

const CUSTOM = new Set(["Metric", "Badge", "Progress", "Callout", "Avatar"]);

// `openUrl` is left out: a card has no business sending the person's browser somewhere on an agent's say-so.
const functions = [...basicCatalog.functions.values()].filter((f) => f.name !== "openUrl");

/** The catalog this chat teaches models and draws: A2UI's basic components, plus Metric, Badge, Progress, Callout and Avatar. */
export const chatCatalog = new Catalog(CATALOG_ID, "v0.9", implementations, functions);

/** The same drawing for a surface that asks for A2UI's standard basic catalog (another agent's, say): it has no extras. */
export const standardCatalog = new Catalog(
  BASIC_CATALOG_ID,
  "v0.9",
  implementations.filter((c) => !CUSTOM.has(c.name)),
  functions,
);
