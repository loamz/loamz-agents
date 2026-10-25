import { describe, expect, it } from "vitest";
import { checkSpec, separateSpecFence, specFromFence, specFromStructured } from "./spec";

const plan = {
  root: "card",
  elements: {
    card: { type: "Card", props: { title: "Plan 3f2a9c1e", description: null, maxWidth: null, centered: null }, children: ["grid", "approve"] },
    grid: { type: "Grid", props: { columns: 2, gap: null }, children: ["equity", "risk"] },
    equity: { type: "Metric", props: { label: "Paper equity", value: "$151,288", detail: null, trend: null }, children: [] },
    risk: { type: "Metric", props: { label: "At risk", value: "$7,624", detail: "5.0% of the account", trend: "down" }, children: [] },
    approve: {
      type: "Button",
      props: { label: "Approve plan", variant: "primary", disabled: null },
      on: { press: { action: "send", params: { text: "Approve plan 3f2a9c1e" } } },
      children: [],
    },
  },
};

describe("checkSpec", () => {
  it("accepts a spec that fits the catalog", () => {
    expect(checkSpec(plan)?.root).toBe("card");
    expect(specFromStructured({ spec: plan })?.root).toBe("card");
  });

  it("drops a spec with a part the catalog does not have", () => {
    const bad = structuredClone(plan);
    bad.elements.equity.type = "Iframe";
    expect(checkSpec(bad)).toBeNull();
  });

  it("drops a part whose props do not fit, and an action the catalog does not have", () => {
    const wrongProps = structuredClone(plan) as typeof plan;
    (wrongProps.elements.risk.props as Record<string, unknown>).value = 7624;
    expect(checkSpec(wrongProps)).toBeNull();
    const unknownAction = structuredClone(plan);
    unknownAction.elements.approve.on.press.action = "deleteEverything";
    expect(checkSpec(unknownAction)).toBeNull();
  });

  it("prunes a child that does not exist instead of failing the whole card", () => {
    const dangling = structuredClone(plan);
    dangling.elements.card.children.push("missing");
    expect(checkSpec(dangling)?.elements.card.children).toEqual(["grid", "approve"]);
  });

  it("repairs `on` written inside props, a common slip", () => {
    const slip = structuredClone(plan) as typeof plan;
    const button = slip.elements.approve as Record<string, unknown>;
    (button.props as Record<string, unknown>).on = button.on;
    delete button.on;
    const fixed = checkSpec(slip);
    expect(fixed?.elements.approve.on).toBeTruthy();
  });

  it("fills the optional props a writer left out, but not the required ones", () => {
    const lean = {
      root: "m",
      elements: { m: { type: "Metric", props: { label: "Equity", value: "$1" }, children: [] } },
    };
    expect(checkSpec(lean)?.elements.m.props).toMatchObject({ label: "Equity", detail: null, trend: null });
    const noValue = { root: "m", elements: { m: { type: "Metric", props: { label: "Equity" }, children: [] } } };
    expect(checkSpec(noValue)).toBeNull();
  });

  it("never throws on rubbish", () => {
    for (const v of [null, undefined, 5, "x", [], {}, { root: 1 }, { spec: 3 }]) {
      expect(checkSpec(v)).toBeNull();
      expect(specFromStructured(v)).toBeNull();
    }
  });
});

describe("specFromFence", () => {
  const lines = [
    { op: "add", path: "/root", value: "card" },
    { op: "add", path: "/elements/card", value: { type: "Card", props: { title: "Hello" }, children: ["m"] } },
    { op: "add", path: "/elements/m", value: { type: "Metric", props: { label: "Equity", value: "$1" }, children: [] } },
  ].map((l) => JSON.stringify(l));

  it("reads JSONL patches, and a whole JSON spec", () => {
    expect(specFromFence(lines.join("\n"))?.elements.m.type).toBe("Metric");
    expect(specFromFence(JSON.stringify(plan))?.root).toBe("card");
  });

  it("draws what has arrived while the last line is still being written", () => {
    const half = lines[0] + "\n" + lines[1] + "\n" + lines[2].slice(0, 30);
    const partial = specFromFence(half);
    expect(partial?.root).toBe("card");
    expect(partial?.elements.card.children).toEqual([]); // the child had not arrived: pruned, not an error
  });

  it("returns null for nothing, prose, or a spec that does not fit", () => {
    expect(specFromFence("")).toBeNull();
    expect(specFromFence("just some words")).toBeNull();
    expect(specFromFence(lines.join("\n").replace("Metric", "Iframe"))).toBeNull();
  });
});

it("puts a glued spec fence on its own line, and leaves a proper one alone", () => {
  expect(separateSpecFence("a takeaway.```spec\n{}\n```")).toBe("a takeaway.\n\n```spec\n{}\n```");
  expect(separateSpecFence("words\n\n```spec\n{}\n```")).toBe("words\n\n```spec\n{}\n```");
});
