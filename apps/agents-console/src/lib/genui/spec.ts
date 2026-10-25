/**
 * Reading a spec safely: whatever a tool or a model sends is checked against the catalog before anything is drawn, and a spec
 * that does not fit is dropped (the chat shows the message's text instead). Nothing here throws.
 */
import { autoFixSpec, compileSpecStream, validateSpec, type Spec } from "@json-render/core";
import { catalog } from "./catalog";

const ACTIONS = new Set([...Object.keys(catalog.data.actions), "setState", "pushState", "removeState", "validateForm"]);

const isExpression = (v: unknown) =>
  !!v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).some((k) => k.startsWith("$"));

/** Every element's props fit its part, and every action it can fire exists. */
function elementsFit(spec: Spec): boolean {
  const parts = catalog.data.components as Record<string, { props: { safeParse: (v: unknown) => { success: boolean; error?: { issues: { path: PropertyKey[] }[] } } } }>;
  for (const element of Object.values(spec.elements)) {
    const part = parts[element.type];
    if (!part) return false;
    const props = (element.props ?? {}) as Record<string, unknown>;
    const dynamic = new Set(Object.keys(props).filter((k) => isExpression(props[k])));
    const result = part.props.safeParse(props);
    // A prop written as an expression ({"$state": "/x"}) is resolved at render time, so it is not judged here.
    if (!result.success && result.error?.issues.some((i) => !dynamic.has(String(i.path[0])))) return false;
    for (const binding of Object.values(element.on ?? {})) {
      for (const b of Array.isArray(binding) ? binding : [binding]) {
        if (!b || !ACTIONS.has((b as { action?: string }).action ?? "")) return false;
      }
    }
  }
  return true;
}

/** Every prop a part declares, `null` where the writer left one out: optional props are all nullable, and a model rarely writes them. */
function withDeclaredProps(spec: Spec): Spec {
  const parts = catalog.data.components as Record<string, { props: { shape?: Record<string, unknown> } }>;
  const elements = Object.fromEntries(
    Object.entries(spec.elements).map(([key, element]) => {
      const declared = Object.keys(parts[element.type]?.props.shape ?? {});
      const props = { ...Object.fromEntries(declared.map((k) => [k, null])), ...(element.props ?? {}) };
      return [key, { ...element, props }];
    }),
  );
  return { ...spec, elements } as Spec;
}

/** A spec that fits the catalog, with the small mistakes models make (`on` or `visible` inside `props`) repaired; else null. */
export function checkSpec(candidate: unknown): Spec | null {
  if (!candidate || typeof candidate !== "object") return null;
  try {
    const { spec: fixed } = autoFixSpec(candidate as Spec);
    const spec = withDeclaredProps(fixed);
    if (validateSpec(spec).issues.some((i) => i.severity === "error")) return null;
    const result = catalog.validate(spec);
    return result.success && elementsFit(spec as Spec) ? (spec as Spec) : null;
  } catch {
    return null;
  }
}

/** The spec in a tool result's `structured_content.spec`, or null. */
export function specFromStructured(structured: unknown): Spec | null {
  if (!structured || typeof structured !== "object") return null;
  return checkSpec((structured as { spec?: unknown }).spec);
}

/**
 * The spec in a ```spec fence a model wrote in its reply: JSONL patches (json-render's stream format), or one whole JSON spec.
 * Called on every chunk while the reply streams, so a half-written last line is ignored and the card grows as lines arrive.
 */
export function specFromFence(source: string): Spec | null {
  const text = source.trim();
  if (!text) return null;
  if (text.startsWith("{") && /"elements"\s*:/.test(text)) {
    try {
      const whole = checkSpec(JSON.parse(text));
      if (whole) return whole;
    } catch {
      // not one whole object: read it as patch lines below
    }
  }
  const lines = text.split("\n").filter((line) => {
    try {
      JSON.parse(line);
      return true;
    } catch {
      return false;
    }
  });
  try {
    return checkSpec(compileSpecStream<Record<string, unknown>>(lines.join("\n")));
  } catch {
    return null;
  }
}

/** A model sometimes glues the fence to its sentence ("...takeaway.```spec"): Markdown only opens a fence at the start of a line. */
export function separateSpecFence(markdown: string): string {
  return markdown.replace(/([^\n])(```spec\b)/g, "$1\n\n$2");
}
