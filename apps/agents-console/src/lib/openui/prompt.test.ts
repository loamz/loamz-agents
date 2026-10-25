import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { buildPrompt } from "./prompt";
import { MEDIA_COMPONENTS, richLibrary, safeMedia } from "./library";

/** The prompt the agent server hands to agents that may write rich answers: generated from OpenUI's library, `pnpm openui:prompt`. */
const FILE = resolve(__dirname, "../../../../../server/server/src/loamz/server/agents/features/directory/agents/openui_prompt.md");

describe("the OpenUI prompt", () => {
  it("is the one the library generates", () => {
    if (process.env.OPENUI_PROMPT_WRITE) writeFileSync(FILE, buildPrompt());
    expect(readFileSync(FILE, "utf8")).toBe(buildPrompt());
  });

  it("frames the language for a chat reply and forbids invented data", () => {
    const text = buildPrompt();
    expect(text).toContain("```openui");
    expect(text).toContain("Never invent figures");
    expect(text).not.toMatch(/realistic\/plausible data/i);
    expect(text).not.toContain("Your ENTIRE response must be valid openui-lang");
    for (const part of ["Card", "ImageBlock", "Carousel", "BarChart", "Table", "TagBlock", "OverviewCardBlock", "FollowUpBlock"]) expect(text).toContain(part);
  });
});

describe("the library", () => {
  it("keeps every component of OpenUI's chat library", () => {
    expect(Object.keys(richLibrary.components).length).toBe(84);
    for (const name of MEDIA_COMPONENTS) expect(richLibrary.components[name]).toBeDefined();
  });

  it("sends a web picture through the proxy and refuses what may not be shown", () => {
    const props = {
      src: "https://example.com/a.png",
      images: [{ src: "https://example.com/b.png", alt: "b" }, { src: "javascript:alert(1)", alt: "x" }, { src: "sandbox:chart.png" }],
      title: "https://this-is-a-title.example",
    };
    const out = safeMedia(props, "t1") as typeof props;
    expect(out.src).toContain("/media/image?url=https%3A%2F%2Fexample.com%2Fa.png");
    expect(out.images[0].src).toContain("/media/image?url=https%3A%2F%2Fexample.com%2Fb.png");
    expect(out.images[2].src).toContain("/workspace/file?thread_id=t1&path=chart.png");
    expect(out.images[1].src).toBe(""); // an address that may not be shown is empty, never passed on
    expect(out.images[1]).toMatchObject({ alt: "x" });
    expect(out.title).toBe("https://this-is-a-title.example"); // text is text
  });
});

describe("the library really is replaced", () => {
  it("uses the guarded components, not OpenUI's", async () => {
    const { openuiChatLibrary } = await import("@openuidev/react-ui/genui-lib");
    const original = (openuiChatLibrary as unknown as { components: Record<string, { component: unknown }> }).components;
    const ours = richLibrary.components as unknown as Record<string, { component: unknown }>;
    for (const name of MEDIA_COMPONENTS) expect(ours[name].component).not.toBe(original[name].component);
    expect(ours.Card.component).toBe(original.Card.component);
  });
});
