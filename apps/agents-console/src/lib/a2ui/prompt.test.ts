import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { checkMessages } from "./check";
import { buildPrompt, componentLines, EXAMPLE_MESSAGES } from "./prompt";

/** The prompt the agent server hands to models that may draw UI. It is generated from the catalog: `pnpm a2ui:prompt`. */
const FILE = resolve(__dirname, "../../../../../server/server/src/loamz/server/agents/features/directory/agents/a2ui_prompt.md");

describe("the A2UI prompt", () => {
  it("is the one the catalog generates", () => {
    if (process.env.A2UI_PROMPT_WRITE) writeFileSync(FILE, buildPrompt());
    expect(readFileSync(FILE, "utf8")).toBe(buildPrompt());
  });

  it("teaches the fence, every component and our rules", () => {
    const text = buildPrompt();
    expect(text).toContain("```a2ui");
    for (const part of ["Text", "Image", "Row", "Column", "List", "Card", "Tabs", "Button", "TextField", "Metric", "Badge", "Progress", "Callout", "Avatar"]) expect(text).toContain(`- ${part}:`);
    expect(text).toContain("Never invent figures");
    expect(text).not.toContain("openUrl");
  });

  it("describes each component with its props, optional ones marked", () => {
    const lines = componentLines();
    expect(lines.find((l) => l.startsWith("- Metric"))).toContain("label: string | {\"path\":\"/x\"}");
    expect(lines.find((l) => l.startsWith("- Image"))).toMatch(/url: .*, description\?: .*, fit\?: "contain"/);
    expect(lines.find((l) => l.startsWith("- Card"))).toContain("child: componentId");
  });

  it("its own example is a valid surface", () => {
    expect(checkMessages(EXAMPLE_MESSAGES)).toHaveLength(3);
  });
});
