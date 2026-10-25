/**
 * Reading A2UI messages safely (https://a2ui.org, v0.9). Whatever a tool or a model sends is checked before the renderer sees it:
 * the shape of each message, how much of it there is, and which catalog a surface asks for. A message that does not fit is
 * dropped and the rest still draw; if nothing fits the chat shows the message's text instead. Nothing here throws.
 */
export type A2uiMessage = { version: "v0.9" } & Record<string, unknown>;

export const CATALOG_ID = "https://loamz.dev/a2ui/chat/v1/catalog.json";
export const BASIC_CATALOG_ID = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json";

const KINDS = ["createSurface", "updateComponents", "updateDataModel", "deleteSurface"] as const;
const MAX_MESSAGES = 200;
const MAX_COMPONENTS = 300;
const MAX_BYTES = 200_000;

const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/** The messages of a payload (an array, or `{messages: [...]}`) that are well formed, with a surface created before it is used. */
export function checkMessages(input: unknown): A2uiMessage[] | null {
  const list = Array.isArray(input) ? input : isObject(input) && Array.isArray(input.messages) ? input.messages : null;
  if (!list || list.length > MAX_MESSAGES) return null;
  let size = 0;
  try {
    size = JSON.stringify(list).length;
  } catch {
    return null;
  }
  if (size > MAX_BYTES) return null;

  const created = new Set<string>();
  const refused = new Set<string>();
  const out: A2uiMessage[] = [];
  let components = 0;
  for (const raw of list) {
    if (!isObject(raw) || raw.version !== "v0.9") continue;
    const kind = KINDS.filter((k) => k in raw);
    if (kind.length !== 1) continue;
    const body = raw[kind[0]];
    if (!isObject(body) || typeof body.surfaceId !== "string" || !body.surfaceId || body.surfaceId.length > 80) continue;
    const id = body.surfaceId;
    if (kind[0] === "createSurface") {
      if (body.catalogId !== CATALOG_ID && body.catalogId !== BASIC_CATALOG_ID) {
        refused.add(id); // a catalog we do not have: the surface cannot be drawn, nor can what follows
        continue;
      }
      created.add(id);
      out.push(raw as A2uiMessage);
      continue;
    }
    if (refused.has(id)) continue;
    if (!created.has(id) && kind[0] !== "deleteSurface") {
      created.add(id); // a model that forgot createSurface: ours is the only catalog it was taught
      out.push({ version: "v0.9", createSurface: { surfaceId: id, catalogId: CATALOG_ID } });
    }
    if (kind[0] === "updateComponents") {
      const comps = Array.isArray(body.components) ? body.components : [];
      components += comps.length;
      if (components > MAX_COMPONENTS) return null; // too much to draw: nothing is better than half a card
      body.components = comps.filter((c) => isObject(c) && typeof c.id === "string" && typeof c.component === "string");
    }
    out.push(raw as A2uiMessage);
  }
  return out.some((m) => "updateComponents" in m) ? out : null;
}

/** The A2UI messages a tool returned for the person's screen (`structured_content.a2ui`), or null when there are none. */
export function messagesFromStructured(structured: unknown): A2uiMessage[] | null {
  return isObject(structured) ? checkMessages(structured.a2ui) : null;
}

/** A model sometimes glues the fence to its sentence ("...takeaway.```openui"): Markdown only opens a fence at the start of a line. */
export function separateFence(markdown: string): string {
  return markdown.replace(/([^\n])(```openui\b)/g, "$1\n\n$2");
}

/** What a card's button says to the agent, as a message from the person. `send` carries the words; any other action is named. */
export function actionToMessage(action: { name: string; context?: Record<string, unknown> }): string {
  const context = action.context ?? {};
  if (action.name === "send" && typeof context.text === "string" && context.text.trim()) return context.text.trim();
  const details = Object.keys(context).length ? ` ${JSON.stringify(context)}` : "";
  return `[card action] ${action.name}${details}`;
}
