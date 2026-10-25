import { describe, expect, it } from "vitest";
import { actionToMessage, BASIC_CATALOG_ID, CATALOG_ID, checkMessages, messagesFromFence, messagesFromStructured, separateFence } from "./check";

const create = { version: "v0.9", createSurface: { surfaceId: "plan", catalogId: CATALOG_ID } };
const components = (n = 2) => ({
  version: "v0.9",
  updateComponents: {
    surfaceId: "plan",
    components: [{ id: "root", component: "Card", child: "t" }, { id: "t", component: "Text", text: "Hello" }].slice(0, n),
  },
});

describe("checkMessages", () => {
  it("accepts a surface, its components and its data", () => {
    const data = { version: "v0.9", updateDataModel: { surfaceId: "plan", path: "/", value: { a: 1 } } };
    expect(checkMessages([create, components(), data])).toHaveLength(3);
    expect(checkMessages({ messages: [create, components()] })).toHaveLength(2);
  });

  it("creates the surface a writer forgot to create", () => {
    const out = checkMessages([components()]);
    expect(out?.[0]).toMatchObject({ createSurface: { surfaceId: "plan", catalogId: CATALOG_ID } });
  });

  it("draws surfaces of the standard basic catalog, and refuses one that asks for a catalog we do not have", () => {
    const basic = { version: "v0.9", createSurface: { surfaceId: "plan", catalogId: BASIC_CATALOG_ID } };
    expect(checkMessages([basic, components()])).toHaveLength(2);
    const other = { version: "v0.9", createSurface: { surfaceId: "plan", catalogId: "https://example.com/other.json" } };
    expect(checkMessages([other, components()])).toBeNull(); // the surface cannot be drawn, nor what follows
  });

  it("drops what is malformed and keeps the rest", () => {
    const junk = [5, null, { version: "v0.8" }, { version: "v0.9" }, { version: "v0.9", createSurface: {}, deleteSurface: {} }];
    expect(checkMessages([...junk, create, components()])).toHaveLength(2);
  });

  it("drops components with no id or type", () => {
    const messy = { version: "v0.9", updateComponents: { surfaceId: "plan", components: [{ id: "root", component: "Card", child: "t" }, { component: "Text" }, "x"] } };
    const out = checkMessages([create, messy]);
    expect((out?.[1].updateComponents as { components: unknown[] }).components).toHaveLength(1);
  });

  it("refuses too much, and nothing to draw", () => {
    const many = Array.from({ length: 301 }, (_, i) => ({ id: `c${i}`, component: "Text", text: "x" }));
    expect(checkMessages([create, { version: "v0.9", updateComponents: { surfaceId: "plan", components: many } }])).toBeNull();
    expect(checkMessages([create])).toBeNull();
    for (const v of [null, undefined, 5, "x", {}, []]) expect(checkMessages(v)).toBeNull();
  });
});

describe("fences", () => {
  const lines = [create, components()].map((m) => JSON.stringify(m));

  it("reads JSONL, and one JSON array", () => {
    expect(messagesFromFence(lines.join("\n"))).toHaveLength(2);
    expect(messagesFromFence(`[${lines.join(",")}]`)).toHaveLength(2);
  });

  it("draws what has arrived while the last line is still being written", () => {
    expect(messagesFromFence(lines[0] + "\n" + lines[1].slice(0, 40))).toBeNull(); // no components yet
    const two = [create, components(1), components(2)].map((m) => JSON.stringify(m));
    expect(messagesFromFence(two[0] + "\n" + two[1] + "\n" + two[2].slice(0, 30))).toHaveLength(2);
  });

  it("returns null for nothing or for prose", () => {
    expect(messagesFromFence("")).toBeNull();
    expect(messagesFromFence("just some words")).toBeNull();
  });

  it("puts a glued fence on its own line, and leaves a proper one alone", () => {
    expect(separateFence("a takeaway.```a2ui\n{}\n```")).toBe("a takeaway.\n\n```a2ui\n{}\n```");
    expect(separateFence("words\n\n```a2ui\n{}\n```")).toBe("words\n\n```a2ui\n{}\n```");
  });
});

describe("tool results and actions", () => {
  it("reads the messages a tool returned beside its text", () => {
    expect(messagesFromStructured({ a2ui: [create, components()] })).toHaveLength(2);
    expect(messagesFromStructured({ result: "text only" })).toBeNull();
    expect(messagesFromStructured(null)).toBeNull();
  });

  it("turns a click into what the person would have typed", () => {
    expect(actionToMessage({ name: "send", context: { text: " Approve plan 3f2a " } })).toBe("Approve plan 3f2a");
    expect(actionToMessage({ name: "submitForm", context: { email: "a@b.c" } })).toBe('[card action] submitForm {"email":"a@b.c"}');
    expect(actionToMessage({ name: "send", context: {} })).toBe("[card action] send");
  });
});
