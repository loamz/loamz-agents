/** What a model is told so it can draw UI in a reply: A2UI v0.9's message format, our catalog (read from the schemas), our rules. */
import { apiCatalog } from "./apis";
import { CATALOG_ID } from "./check";

type Schema = { $ref?: string; enum?: unknown[]; type?: string; items?: Schema; properties?: Record<string, Schema>; const?: unknown; required?: string[]; anyOf?: Schema[]; oneOf?: Schema[] };

const REFS: Record<string, string> = {
  DynamicString: 'string | {"path":"/x"}',
  DynamicNumber: 'number | {"path":"/x"}',
  DynamicBoolean: 'boolean | {"path":"/x"}',
  DynamicStringList: '[string] | {"path":"/x"}',
  DataBinding: '{"path":"/x"}',
  ComponentId: "componentId",
  ChildList: '[componentId] | {"path":"/list","componentId":"template"}',
  Action: '{"event":{"name":"send","context":{"text":"..."}}}',
  Checkable: "",
  AccessibilityAttributes: "",
};

function typeOf(s: Schema): string {
  if (s.$ref) return REFS[s.$ref.split("/").pop() ?? ""] ?? "any";
  if (s.enum) return s.enum.map((v) => JSON.stringify(v)).join("|");
  if (s.type === "array" && s.items) return `[${typeOf(s.items)}]`;
  if (s.type === "object" && s.properties) return `{${Object.entries(s.properties).map(([k, v]) => `${k}: ${typeOf(v)}`).join(", ")}}`;
  if (s.anyOf || s.oneOf) return (s.anyOf ?? s.oneOf ?? []).map(typeOf).join(" | ");
  return s.type ?? "any";
}

/** One line per component: `Text(text, variant?)`, the props in the order the schema lists them, `?` for optional. */
export function componentLines(): string[] {
  const doc = apiCatalog.toJson() as { components: Record<string, { allOf?: Schema[]; properties?: Record<string, Schema>; required?: string[] }> };
  return Object.entries(doc.components).map(([name, def]) => {
    const body = def.allOf ? (def.allOf.find((a) => a.properties?.component) ?? {}) : def;
    const required = new Set(body.required ?? []);
    const props = Object.entries(body.properties ?? {})
      .filter(([k]) => !["component", "accessibility", "weight", "checks"].includes(k))
      .map(([k, v]) => `${k}${required.has(k) ? "" : "?"}: ${typeOf(v)}`);
    return `- ${name}: { ${props.join(", ")} }`;
  });
}

export const RULES = [
  "Draw a UI only when it helps: a plan to approve, a comparison, a dashboard, a set of numbers, a picture. A greeting, a short answer or an explanation stays plain text.",
  "Every number, name and date in a UI must come from a tool result or from the person's messages in this conversation. Never invent figures to fill a layout.",
  "A Button's action must be {\"event\":{\"name\":\"send\",\"context\":{\"text\":\"...\"}}}: its text is sent to you as if the person had typed it. Do not add a button that approves, buys, sells, sends or deletes unless the person asked for that step.",
  "Pictures: use a file the code interpreter saved (url \"sandbox:chart.png\") or an https address that appeared in a tool result. Never make up an address.",
  "Keep it small: about 40 components at most, one surface per reply, and a short surfaceId such as \"plan\". Reusing a surfaceId replaces that surface; updateDataModel changes its values.",
  "Write the messages once, inside the ```a2ui fence, after one or two sentences of plain text. Do not repeat the UI's content in prose.",
];

const EXAMPLE = [
  { version: "v0.9", createSurface: { surfaceId: "summary", catalogId: CATALOG_ID } },
  {
    version: "v0.9",
    updateComponents: {
      surfaceId: "summary",
      components: [
        { id: "root", component: "Card", child: "body" },
        { id: "body", component: "Column", children: ["title", "numbers", "orders", "go"] },
        { id: "title", component: "Text", text: "Account", variant: "h3" },
        { id: "numbers", component: "Row", children: ["equity", "today"] },
        { id: "equity", component: "Metric", label: "Equity", value: { path: "/equity" } },
        { id: "today", component: "Metric", label: "Today", value: "+$412", trend: "up" },
        { id: "orders", component: "List", children: { path: "/orders", componentId: "order" } },
        { id: "order", component: "Row", children: ["sym", "side"] },
        { id: "sym", component: "Text", text: { path: "symbol" } },
        { id: "side", component: "Badge", text: { path: "side" }, tone: "success" },
        { id: "go", component: "Button", variant: "primary", child: "golabel", action: { event: { name: "send", context: { text: "Show me the details" } } } },
        { id: "golabel", component: "Text", text: "Details" },
      ],
    },
  },
  { version: "v0.9", updateDataModel: { surfaceId: "summary", path: "/", value: { equity: "$151,288", orders: [{ symbol: "AAPL", side: "BUY" }, { symbol: "XOM", side: "BUY" }] } } },
];

export const EXAMPLE_MESSAGES = EXAMPLE;

export function buildPrompt(): string {
  return `# Drawing UI in a reply (A2UI)

When a picture, table or dashboard answers better than words, draw it with A2UI (https://a2ui.org, v0.9). After one or two sentences of plain text, write the UI as A2UI messages, one JSON object per line, inside a fence named a2ui:

\`\`\`a2ui
${EXAMPLE.map((m) => JSON.stringify(m)).join("\n")}
\`\`\`

## Messages
- \`createSurface\`: starts a surface. Always first; catalogId is "${CATALOG_ID}".
- \`updateComponents\`: a flat list of components, each \`{"id", "component", ...props}\`. One component must have the id \`root\`. A parent names its children by id (\`child\` for one, \`children\` for several). Components may come in any order.
- \`updateDataModel\`: puts data at a JSON pointer \`path\` ("/" is everything). A prop can read it with \`{"path":"/equity"}\`. Inside a List template a relative path ("symbol") reads the current item.
- \`deleteSurface\`: removes a surface.
Every message has \`"version":"v0.9"\`.

## Components
${componentLines().join("\n")}

Layout: Card holds one child, Column and Row hold several. A List repeats its template once per item of an array in the data model. Text variants h1-h5, body, caption; Text supports **bold**, *italic* and \`code\`. A Metric's trend colours its number. Divider separates.

## Rules
${RULES.map((r, i) => `${i + 1}. ${r}`).join("\n")}
`;
}
