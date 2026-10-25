# Generative UI

An agent's answer can be more than text: a plan to approve, an account, a comparison, a dashboard. The console draws these
from a **json-render spec** ([json-render.dev](https://json-render.dev)): a small JSON tree of standard parts (Card, Grid,
Metric, Table, Badge, Progress, Button, ...) that the console renders with its own design system.

Two things can produce a spec:

| Who | How | When to use |
|---|---|---|
| **A tool** | Returns `structuredContent: {"spec": {...}}` beside its text. | Anything the tool already knows exactly (a plan, an account). The numbers come from code, never from the model. |
| **The model** | Writes a `spec` fence in its reply: a sentence, then JSONL patches. | One-off layouts the model composes from what it has learnt in the conversation. |

## Why json-render

- The model writes a vocabulary it already knows (`catalog.prompt()` teaches it the parts and the format); a hand-made schema
  would be one more thing to get wrong.
- The spec is data. The console checks it against the catalog before drawing anything; a spec that does not fit is dropped and
  the message's text still shows. A mistake costs the card, never the chat.
- A2UI (Google) is the same idea as a wire protocol. json-render is the renderer here; A2UI messages can be converted to a spec
  when a use needs them.

## The catalog

`src/lib/genui/catalog.ts`. The parts and props are json-render's shadcn definitions, minus `className` (a model styles with
props, not classes), plus three additions: `Badge.tone`, `Progress.tone` and `Metric`. The only action is `send`: a button puts
text in the chat as if the person had typed it, so the agent's own tools and approvals still apply to whatever it asks for.

Props a writer leaves out are read as `null`. The checker (`src/lib/genui/spec.ts`) also repairs the usual slips (`on` or
`visible` written inside `props`), prunes children that do not exist, and drops unknown parts, bad props and unknown actions.

## Teaching an agent to draw

Add `generative_ui` to the agent's tools. Its instructions then include `genui_prompt.md`, generated from the catalog:

```bash
pnpm --filter agents-console genui:prompt    # after changing catalog.ts or the rules in prompt.ts
```

A test fails when the committed prompt and the catalog differ. The prompt costs about 5,000 tokens, so it is opt-in.

## Returning a card from a tool (Python)

`loamz-solutions` has a tiny builder (`kit/genui.py`):

```python
from loamz.solutions.kit.genui import Reply, node, send, spec

card = spec(node("Card",
    node("Grid", node("Metric", label="Equity", value="$151,288"), columns=2),
    node("Button", label="Approve plan 3f2a9c1e", variant="primary", on=send("Approve plan 3f2a9c1e")),
    title="Plan 3f2a9c1e"))
return Reply.shown("Plan 3f2a9c1e: ...", card)   # text for the model, card for the person
```

Through MCP the result is a `CallToolResult` with `structuredContent={"spec": card}`; the engine carries it to the chat
(`tool.result.structured_content`) and the console draws it, live and when the conversation is reloaded.

## What a card cannot do

No script, no HTML, no classes, no links to the outside, no images from elsewhere (an Avatar shows initials only). A button only
sends text. A card from an old message can still be clicked: whatever it asks for goes through the agent's checks again (the
trading desk, for example, refuses an approval for any plan that is not the current one).
