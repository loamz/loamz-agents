import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { buildPrompt } from "./prompt";

/** The prompt the agent server hands to models that may draw UI. It is generated from the catalog: `pnpm genui:prompt`. */
const FILE = resolve(__dirname, "../../../../../server/server/src/loamz/server/agents/features/directory/agents/genui_prompt.md");

describe("the generative UI prompt", () => {
  it("is the one the catalog generates", () => {
    if (process.env.GENUI_PROMPT_WRITE) writeFileSync(FILE, buildPrompt());
    expect(readFileSync(FILE, "utf8")).toBe(buildPrompt());
  });

  it("tells the model the fence, the parts and our rules", () => {
    const text = buildPrompt();
    expect(text).toContain("```spec");
    for (const part of ["Card", "Metric", "Table", "Button", "Progress"]) expect(text).toContain(part);
    expect(text).toContain("`send`");
    expect(text).not.toContain("className");
  });
});
