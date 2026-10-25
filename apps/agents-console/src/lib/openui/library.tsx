"use client";

/**
 * OpenUI's chat component library (https://openui.com, MIT) with one change: every picture address a model writes goes through
 * `resolveMedia` (workspace file, small data image, or the server's picture proxy), never straight to the browser. Everything else is
 * OpenUI's own, so a model that knows its language writes it as documented.
 */
import { openuiChatLibrary } from "@openuidev/react-ui/genui-lib";
import { createContext, useContext, type ComponentType } from "react";
import { resolveMedia } from "@/lib/a2ui/media";

/** What a drawn answer needs from the conversation around it: whose workspace its files come from. */
export const OpenUiEnv = createContext<{ threadId?: string | null }>({});

/** The components whose props carry a picture address, and the props that do. */
export const MEDIA_COMPONENTS = ["Image", "ImageBlock", "ImageGallery", "ImageText", "ImageTextLarge", "ContextCardItem", "VisualCardItem", "ListItem"];
const ADDRESS_KEYS = new Set(["src", "url", "image", "imageUrl", "bgImageSrc"]);

/**
 * The props with every picture address resolved. An address is a string under a picture key (`src`, `image`, ...) or an item of an
 * `images` list; what resolveMedia will not show (any other scheme, `javascript:`, plain http) becomes empty, so a component never
 * gets an address that was not checked. Text next to it (`alt`, titles) is left alone.
 */
export function safeMedia(value: unknown, threadId?: string | null, key = ""): unknown {
  if (typeof value === "string") return ADDRESS_KEYS.has(key) || key === "images" ? (resolveMedia(value, "image", threadId) ?? "") : value;
  if (Array.isArray(value)) return value.map((v) => safeMedia(v, threadId, key));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, safeMedia(v, threadId, k)]));
  return value;
}

type Defined = { name: string; props: unknown; description: string; component: ComponentType<{ props: Record<string, unknown> }> };
type Extensible = { components: Record<string, Defined>; extend: (extension: { components: { override: Defined[] } }) => typeof openuiChatLibrary };

function guarded(definition: Defined): Defined {
  const Original = definition.component;
  // A component defined once, here: markdown would rebuild everything under a new function each render.
  function Guarded(p: { props: Record<string, unknown> }) {
    const { threadId } = useContext(OpenUiEnv);
    return <Original {...p} props={safeMedia(p.props, threadId) as Record<string, unknown>} />;
  }
  return { ...definition, component: Guarded as Defined["component"] };
}

const source = openuiChatLibrary as unknown as Extensible;

export const richLibrary = source.extend({
  components: { override: MEDIA_COMPONENTS.flatMap((name) => (source.components[name] ? [guarded(source.components[name])] : [])) },
});
