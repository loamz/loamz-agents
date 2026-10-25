// @vitest-environment happy-dom
import { createRoot, type Root } from "react-dom/client";
import { act, createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import OpenUiBlock, { actionToSend } from "./OpenUiBlock";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// The charts measure their labels on a canvas, which the test DOM does not have.
HTMLCanvasElement.prototype.getContext = (() => ({ font: "", measureText: (text: string) => ({ width: text.length * 6 }) })) as never;

let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  document.body.innerHTML = "";
});

const BRIEFING = [
  'root = Card([header, hero, tags, chart, picks, follow])',
  'header = CardHeader("Momentum briefing", "What the strategy holds at the next open")',
  'hero = ImageBlock("https://example.com/hero.png", "A hero picture")',
  'tags = TagBlock(["8 buys", "1 sell"])',
  'chart = BarChart(["MRK", "LLY"], [s1], "grouped", "Symbol", "Momentum (%)")',
  's1 = Series("12-1M", [74, 46])',
  'picks = Carousel([[t1, i1], [t2, i2]], "card")',
  't1 = TextContent("Energy")',
  'i1 = ImageBlock("sandbox:energy.png", "Energy")',
  't2 = TextContent("Bad")',
  'i2 = ImageBlock("javascript:alert(1)", "Bad")',
  'follow = FollowUpBlock([f1])',
  'f1 = FollowUpItem("Rank the buys by momentum-to-risk")',
].join("\n");

function draw(source: string, onSend: (t: string) => void = () => {}, streaming = false) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(createElement(OpenUiBlock, { source, onSend, streaming, threadId: "t1" })));
  return host;
}

describe("OpenUiBlock", () => {
  it("draws a model's answer with OpenUI's own components", () => {
    const host = draw(BRIEFING);
    const text = host.textContent ?? "";
    for (const expected of ["Momentum briefing", "What the strategy holds", "8 buys", "1 sell", "Energy", "Rank the buys by momentum-to-risk"]) expect(text).toContain(expected);
    expect(host.querySelector("[data-openui]")).not.toBeNull();
    expect(host.querySelector("svg")).not.toBeNull(); // the chart
  });

  it("never loads a picture from the address the model wrote", () => {
    const host = draw(BRIEFING);
    const sources = [...host.querySelectorAll("img")].map((i) => i.getAttribute("src") ?? "");
    expect(sources.some((s) => s.includes("/media/image?url=https%3A%2F%2Fexample.com%2Fhero.png"))).toBe(true);
    expect(sources.some((s) => s.includes("/workspace/file?thread_id=t1&path=energy.png"))).toBe(true);
    expect(sources.every((s) => !s.startsWith("https://") && !s.startsWith("http://") && !s.startsWith("javascript:"))).toBe(true);
  });

  it("a follow-up suggestion sends its words to the agent", () => {
    const sent: string[] = [];
    const host = draw(BRIEFING, (t) => sent.push(t));
    const item = [...host.querySelectorAll("*")].find((e) => e.children.length === 0 && e.textContent === "Rank the buys by momentum-to-risk")!;
    act(() => (item.closest("button, [role=button], li, div") as HTMLElement).click());
    expect(sent).toEqual(["Rank the buys by momentum-to-risk"]);
  });

  it("draws what has arrived while the answer is still streaming", () => {
    const half = BRIEFING.split("\n").slice(0, 3).join("\n");
    const host = draw(half, undefined, true);
    expect(host.textContent).toContain("Momentum briefing");
  });
});

describe("actionToSend", () => {
  it("sends a follow-up, opens only https links, and names anything else", () => {
    const opened: string[] = [];
    const open = (url: string) => opened.push(url);
    expect(actionToSend({ type: "continue_conversation", humanFriendlyMessage: "Tell me more" }, open)).toBe("Tell me more");
    expect(actionToSend({ type: "open_url", params: { url: "https://example.com/a" } }, open)).toBeNull();
    expect(actionToSend({ type: "open_url", params: { url: "javascript:alert(1)" } }, open)).toBeNull();
    expect(actionToSend({ type: "open_url", params: { url: "http://example.com" } }, open)).toBeNull();
    expect(opened).toEqual(["https://example.com/a"]);
    expect(actionToSend({ type: "custom_thing", humanFriendlyMessage: "Pick AAPL" }, open)).toBe("[card action] Pick AAPL");
    expect(actionToSend({}, open)).toBeNull();
  });
});


