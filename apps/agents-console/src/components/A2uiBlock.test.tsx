// @vitest-environment happy-dom
import { createRoot, type Root } from "react-dom/client";
import { act, createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import A2uiBlock from "./A2uiBlock";
import { CATALOG_ID, type A2uiMessage } from "@/lib/a2ui/check";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  document.body.innerHTML = "";
});

function draw(messages: A2uiMessage[], onSend: (t: string) => void = () => {}) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(createElement(A2uiBlock, { messages, onSend, threadId: "t1" })));
  return host;
}

const surface = (components: object[], data?: object): A2uiMessage[] => [
  { version: "v0.9", createSurface: { surfaceId: "s", catalogId: CATALOG_ID } },
  { version: "v0.9", updateComponents: { surfaceId: "s", components } },
  ...(data ? [{ version: "v0.9" as const, updateDataModel: { surfaceId: "s", path: "/", value: data } }] : []),
];

describe("A2uiBlock", () => {
  it("draws text, our extra parts, and list rows from the data model", () => {
    const host = draw(
      surface(
        [
          { id: "root", component: "Card", child: "col" },
          { id: "col", component: "Column", children: ["h", "m", "b", "p", "rows"] },
          { id: "h", component: "Text", text: { path: "/title" }, variant: "h3" },
          { id: "m", component: "Metric", label: "Equity", value: "$151,288", trend: "up" },
          { id: "b", component: "Badge", text: "SELL", tone: "danger" },
          { id: "p", component: "Progress", value: 15, label: "Share of the book" },
          { id: "rows", component: "List", children: { path: "/orders", componentId: "row" } },
          { id: "row", component: "Text", text: { path: "symbol" } },
        ],
        { title: "Plan 3f2a", orders: [{ symbol: "AAPL" }, { symbol: "XOM" }] },
      ),
    );
    const text = host.textContent ?? "";
    for (const expected of ["Plan 3f2a", "Equity", "$151,288", "SELL", "Share of the book", "15%", "AAPL", "XOM"]) expect(text).toContain(expected);
    expect(host.querySelector("[role=progressbar]")?.getAttribute("aria-valuenow")).toBe("15");
  });

  it("a button sends its words to the agent", () => {
    const sent: string[] = [];
    const host = draw(
      surface([
        { id: "root", component: "Button", child: "l", variant: "primary", action: { event: { name: "send", context: { text: "Approve plan 3f2a" } } } },
        { id: "l", component: "Text", text: "Approve" },
      ]),
      (t) => sent.push(t),
    );
    act(() => host.querySelector("button")!.click());
    expect(sent).toEqual(["Approve plan 3f2a"]);
  });

  it("a button resolves the data it carries, and an unknown action is named", () => {
    const sent: string[] = [];
    const host = draw(
      surface(
        [
          { id: "root", component: "Button", child: "l", action: { event: { name: "pick", context: { symbol: { path: "/chosen" } } } } },
          { id: "l", component: "Text", text: "Pick" },
        ],
        { chosen: "XOM" },
      ),
      (t) => sent.push(t),
    );
    act(() => host.querySelector("button")!.click());
    expect(sent).toEqual(['[card action] pick {"symbol":"XOM"}']);
  });

  it("pictures never load from where an agent said: workspace files and the proxy only", () => {
    const host = draw(
      surface([
        { id: "root", component: "Column", children: ["a", "b", "c", "d"] },
        { id: "a", component: "Image", url: "sandbox:chart.png" },
        { id: "b", component: "Image", url: "https://example.com/logo.png" },
        { id: "c", component: "Image", url: "http://example.com/plain.png" },
        { id: "d", component: "Image", url: "javascript:alert(1)" },
      ]),
    );
    const sources = [...host.querySelectorAll("img")].map((i) => i.getAttribute("src") ?? "");
    expect(sources).toHaveLength(2);
    expect(sources[0]).toContain("/workspace/file?thread_id=t1&path=chart.png");
    expect(sources[1]).toContain("/media/image?url=https%3A%2F%2Fexample.com%2Flogo.png");
    expect(host.innerHTML).not.toContain("javascript:");
    expect(host.innerHTML).not.toContain('src="http://');
  });

  it("tabs switch what is shown", () => {
    const host = draw(
      surface([
        { id: "root", component: "Tabs", tabs: [{ title: "One", child: "a" }, { title: "Two", child: "b" }] },
        { id: "a", component: "Text", text: "first panel" },
        { id: "b", component: "Text", text: "second panel" },
      ]),
    );
    expect(host.textContent).toContain("first panel");
    expect(host.textContent).not.toContain("second panel");
    act(() => [...host.querySelectorAll("[role=tab]")][1].dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 })));
    expect(host.textContent).toContain("second panel");
  });

  it("a component the catalog does not have costs itself, not the card", () => {
    const host = draw(
      surface([
        { id: "root", component: "Column", children: ["t", "x"] },
        { id: "t", component: "Text", text: "still here" },
        { id: "x", component: "Iframe", src: "https://evil.example" },
      ]),
    );
    expect(host.textContent).toContain("still here");
    expect(host.innerHTML).not.toContain("iframe");
  });
});
