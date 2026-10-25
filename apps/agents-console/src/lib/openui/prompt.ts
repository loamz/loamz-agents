/** What a model is told so it can draw a rich answer in OpenUI Lang, inside a chat reply: OpenUI's own prompt for its chat library, with our framing. */
import { openuiChatLibrary } from "@openuidev/react-ui/genui-lib";
import { openuiChatPromptOptions } from "@openuidev/react-ui/genui-lib/prompt-options";

const options = openuiChatPromptOptions as unknown as { examples: string[]; additionalRules: string[] };

export const PREAMBLE =
  "You answer in a chat. Most replies are plain text. When a visual, structured answer is clearly better (a briefing, a comparison, a dashboard, a set of numbers, pictures), " +
  "write one or two sentences of plain text, then the UI in openui-lang inside a fenced block named openui, like this:\n\n```openui\nroot = Card([...])\n```\n\n" +
  "Inside the fence the language rules below apply exactly. Outside it, write normal text. Write at most one openui block per reply.";

/** Their rule that invents data contradicts ours: nothing in a UI may be made up. */
const REPLACED = /realistic\/plausible data/i;

export const RULES = [
  "Every number, name, date and address in a UI must come from a tool result or from the person's messages in this conversation. Never invent figures or data to fill a layout.",
  "Pictures: use a file the code interpreter saved (src \"sandbox:chart.png\") or an https address the person gave you or that a tool (such as image_search) returned. Never make up an address.",
  "Do not repeat the UI's content in the plain text around it.",
];

export function buildPrompt(): string {
  const library = openuiChatLibrary as unknown as { prompt: (o: unknown) => string };
  return (
    library.prompt({
      preamble: PREAMBLE,
      examples: options.examples,
      additionalRules: [...options.additionalRules.filter((r) => !REPLACED.test(r)), ...RULES],
    }).trim() + "\n"
  );
}
