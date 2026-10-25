// @vitest-environment happy-dom
/**
 * Hostile content through every renderer: chat Markdown, A2UI cards from tools and OpenUI pages from models. Whatever it says, what
 * reaches the page must hold no script, frame, plugin, event handler or javascript:/data: address a click or load could run.
 */
import { createRoot, type Root } from "react-dom/client";
import { act, createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { Markdown } from "@/components/groups/Markdown";
import { MessageBubble } from "@/components/MessageBubble";
import A2uiBlock from "@/components/A2uiBlock";
import OpenUiBlock from "@/components/OpenUiBlock";
import { CATALOG_ID, checkMessages, type A2uiMessage } from "@/lib/a2ui/check";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
HTMLCanvasElement.prototype.getContext = (() => ({ font: "", measureText: (t: string) => ({ width: t.length * 6 }) })) as never;

const PAYLOADS = [
  "<script>window.__pwned=1</script>",
  "<img src=x onerror=window.__pwned=1>",
  "<svg onload=window.__pwned=1><circle/></svg>",
  "<iframe src='javascript:window.__pwned=1'></iframe>",
  "<a href='javascript:window.__pwned=1'>x</a>",
  "<object data='data:text/html,<script>1</script>'></object><embed src='x'>",
  "[click](javascript:window.__pwned=1)",
  "[click](JaVaScRiPt:window.__pwned=1)",
  "[click](  javascript:window.__pwned=1)",
  "[click](data:text/html;base64,PHNjcmlwdD4xPC9zY3JpcHQ+)",
  "[click](vbscript:msgbox(1))",
  "![x](javascript:window.__pwned=1)",
  "![x](x \"onerror=window.__pwned=1\")",
  "<details open ontoggle=window.__pwned=1>",
  "<style>@import 'https://evil.example/x.css'</style>",
  "<meta http-equiv='refresh' content='0;url=javascript:1'>",
  "`</code><script>window.__pwned=1</script>`",
  "```html\n<script>window.__pwned=1</script>\n```",
  "$$\\href{javascript:window.__pwned=1}{x}$$",
];

let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  document.body.innerHTML = "";
  delete (window as { __pwned?: number }).__pwned;
});

function mount(node: React.ReactElement) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(node));
  return host;
}

function assertInert(host: HTMLElement, label: string) {
  expect(host.querySelectorAll("script, iframe, object, embed, meta, base, link, style[src]").length, `${label}: executable element`).toBe(0);
  for (const el of host.querySelectorAll("*")) {
    for (const attr of el.getAttributeNames()) {
      expect(attr.toLowerCase().startsWith("on"), `${label}: ${el.tagName} has ${attr}`).toBe(false);
      const value = (el.getAttribute(attr) ?? "").trim().toLowerCase();
      if (["href", "src", "action", "formaction", "xlink:href", "data", "poster"].includes(attr.toLowerCase())) {
        expect(/^(javascript|vbscript):/.test(value), `${label}: ${el.tagName}[${attr}]=${value}`).toBe(false);
        expect(/^data:(?!image\/(png|jpe?g|gif|webp);base64,)/.test(value), `${label}: ${el.tagName}[${attr}]=${value.slice(0, 40)}`).toBe(false);
      }
    }
  }
  expect((window as { __pwned?: number }).__pwned, `${label}: ran script`).toBeUndefined();
}

describe("chat Markdown", () => {
  for (const payload of PAYLOADS) {
    it(`is inert: ${payload.slice(0, 48)}`, () => {
      assertInert(mount(createElement(Markdown, null, `Before ${payload} after`)), "markdown");
    });
  }
});

const surface = (components: object[]): A2uiMessage[] => [
  { version: "v0.9", createSurface: { surfaceId: "s", catalogId: CATALOG_ID } },
  { version: "v0.9", updateComponents: { surfaceId: "s", components } },
];

describe("A2UI cards", () => {
  for (const payload of PAYLOADS) {
    it(`is inert: ${payload.slice(0, 48)}`, () => {
      const messages = surface([
        { id: "root", component: "Card", child: "col" },
        { id: "col", component: "Column", children: ["t", "c", "m", "b", "i", "a", "v"] },
        { id: "t", component: "Text", text: payload },
        { id: "c", component: "Callout", title: payload, text: payload },
        { id: "m", component: "Metric", label: payload, value: payload, detail: payload },
        { id: "b", component: "Badge", text: payload },
        { id: "i", component: "Image", url: payload.replace(/^\[.*?\]\(|\)$/g, ""), description: payload },
        { id: "a", component: "Avatar", name: payload },
        { id: "v", component: "Video", url: payload },
      ]);
      assertInert(mount(createElement(A2uiBlock, { messages, threadId: "t1" })), "a2ui");
    });
  }

  it("refuses a surface that asks for a catalog we do not have, and a component it does not know", () => {
    const foreign = [{ version: "v0.9", createSurface: { surfaceId: "s", catalogId: "https://evil.example/catalog.json" } }, ...surface([{ id: "root", component: "Text", text: "x" }]).slice(1)];
    expect(checkMessages(foreign)).toBeNull();
    const host = mount(createElement(A2uiBlock, { messages: surface([{ id: "root", component: "Iframe", src: "https://evil.example" }]), threadId: "t1" }));
    assertInert(host, "unknown component");
  });
});

describe("OpenUI pages", () => {
  for (const payload of PAYLOADS) {
    it(`is inert: ${payload.slice(0, 48)}`, () => {
      const text = JSON.stringify(payload);
      const url = JSON.stringify(payload.replace(/^\[.*?\]\(|\)$/g, "").replace(/^<a href='|'>x<\/a>$/g, ""));
      const source = [
        "root = Card([h, t, tags, img, gallery, callout, follow])",
        `h = CardHeader(${text}, ${text})`,
        `t = TextContent(${text})`,
        `tags = TagBlock([${text}])`,
        `img = ImageBlock(${url}, ${text})`,
        `gallery = ImageGallery([{src: ${url}, alt: ${text}}])`,
        `callout = Callout("warning", ${text}, ${text})`,
        `follow = FollowUpBlock([FollowUpItem(${text})])`,
      ].join("\n");
      assertInert(mount(createElement(OpenUiBlock, { source, threadId: "t1" })), "openui");
    });
  }
});

describe("the main chat bubble (Markdown, math, diagrams)", () => {
  for (const payload of PAYLOADS) {
    it(`is inert: ${payload.slice(0, 48)}`, () => {
      const host = mount(createElement(MessageBubble, { role: "assistant", content: `Before ${payload} after`, timestamp: new Date() } as never));
      assertInert(host, "bubble");
    });
  }
});

describe("the check itself", () => {
  it("fails on a live handler, a script and a javascript: link (a control, so a green run means something)", () => {
    for (const html of ["<img src=x onerror=alert(1)>", "<script>1</script>", "<a href='javascript:1'>x</a>", "<img src='data:text/html;base64,AAAA'>"]) {
      const host = document.createElement("div");
      host.innerHTML = html;
      expect(() => assertInert(host, "control")).toThrow();
    }
  });
});
