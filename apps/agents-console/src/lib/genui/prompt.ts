/** What a model is told so it can draw UI in a reply: json-render's own inline-mode prompt for our catalog, plus our rules. */
import { catalog } from "./catalog";

export const RULES = [
  "Draw a UI only when it helps: a plan to approve, a comparison, a dashboard, a set of numbers. A greeting, a short answer or an explanation stays plain text.",
  "Every number, name and date in a UI must come from a tool result or from the person's messages in this conversation. Never invent figures to fill a layout.",
  "A Button may only use the `send` action, and its text must be a message the person could have typed. Do not add a button that approves, buys, sells, sends or deletes unless the person asked for that step.",
  "Headline numbers: a Card with a Grid of Metric parts. Rows of records: a Table. Warnings: an Alert. Shares or limits used: a Progress.",
  "Keep it small: about 40 elements at most, and only the props you need.",
  "Write the spec once, inside the ```spec fence, after one or two sentences of plain text. Do not repeat the spec's content in prose.",
];

export function buildPrompt(): string {
  return catalog.prompt({ mode: "inline", customRules: RULES } as never).trim() + "\n";
}
