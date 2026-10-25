// @vitest-environment happy-dom
import { createRoot, type Root } from "react-dom/client";
import { act, createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { GroupCard, titleOf } from "./GroupCard";
import { CATALOG_ID, type A2uiMessage } from "@/lib/a2ui/check";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const plan: A2uiMessage[] = [
  { version: "v0.9", createSurface: { surfaceId: "plan", catalogId: CATALOG_ID } },
  {
    version: "v0.9",
    updateComponents: {
      surfaceId: "plan",
      components: [
        { id: "root", component: "Card", child: "col" },
        { id: "col", component: "Column", children: ["h", "b"] },
        { id: "h", component: "Text", text: "Plan 3f2a", variant: "h3" },
        { id: "b", component: "Button", child: "l", action: { event: { name: "send", context: { text: "Approve plan 3f2a" } } } },
        { id: "l", component: "Text", text: "Approve" },
      ],
    },
  },
];

let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  document.body.innerHTML = "";
});

function draw(ui: unknown, onSend: (t: string) => void = () => {}) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(createElement(GroupCard, { ui, author: "Paper trader", onSend })));
  return host;
}

describe("GroupCard", () => {
  it("is folded to its title until opened", async () => {
    const host = draw(plan);
    expect(host.textContent).toContain("Plan 3f2a");
    expect(host.querySelector("[data-a2ui]")).toBeNull();
    act(() => host.querySelector("button")!.click());
    await act(async () => new Promise((r) => setTimeout(r, 200)));
    expect(host.querySelector("[data-a2ui]")).not.toBeNull();
  });

  it("a button speaks to the member who showed it", async () => {
    const sent: string[] = [];
    const host = draw(plan, (t) => sent.push(t));
    act(() => host.querySelector("button")!.click());
    await act(async () => new Promise((r) => setTimeout(r, 200)));
    act(() => [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("Approve"))!.click());
    expect(sent).toEqual(["@Paper trader Approve plan 3f2a"]);
  });

  it("shows nothing for a payload that is not a surface", () => {
    expect(draw([{ junk: true }]).textContent).toBe("");
    expect(draw(null).textContent).toBe("");
  });

  it("names a surface by its first heading", () => {
    expect(titleOf(plan)).toBe("Plan 3f2a");
    expect(titleOf([])).toBe("Card");
  });
});
