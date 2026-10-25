# UI in a message (A2UI)

An agent's answer can be more than text: a plan to approve, an account, a comparison, a picture. It is drawn from **A2UI**
([a2ui.org](https://a2ui.org), v0.9): a small set of JSON messages (`createSurface`, `updateComponents`, `updateDataModel`) that
the console checks and draws with its own components. Nothing is executed: a card is data.

| Who | How | When |
|---|---|---|
| **A tool** | Returns `structuredContent: {"a2ui": [messages]}` beside its text. | Anything the tool already knows exactly (a plan, an account). The numbers come from code, never from the model. |
| **The model** | Writes an `a2ui` fence in its reply: a sentence, then one message per line. | A layout it composes from what it learnt in the conversation. |

Both are drawn the same way, live, while a reply streams, and when a conversation is reloaded.

## Why A2UI

It is a protocol, not a library: surfaces with ids, a data model that can change after the card is shown, actions that carry their
resolved data back, and a catalog the client negotiates. Another agent can send us UI in it, and the same messages draw on other
clients. The renderer here is ours (design system, media policy); the schemas, data binding, list templates and actions are A2UI's
(`@a2ui/web_core`, `@a2ui/react`).

## The catalog

`src/lib/a2ui/`. A2UI's basic components (Text, Image, Icon, Video, AudioPlayer, Row, Column, List, Card, Tabs, Modal, Divider, Button,
TextField, CheckBox, ChoicePicker, Slider, DateTimeInput) plus five of ours: **Metric**, **Badge**, **Progress**, **Callout**, **Avatar**.
A surface may name our catalog or the standard basic one. `openUrl` is not offered.

`check.ts` runs before anything is drawn: the shape of each message, how much there is, and which catalog it asks for. A message that
does not fit is dropped and the rest still draw; if nothing fits, the message's text shows instead.

## What a card can do

A button's action is `{"event": {"name": "send", "context": {"text": "..."}}}`: the text goes into the chat as if the person typed it,
so the agent's own tools and approvals still apply. Any other action name arrives as `[card action] name {context}`. In a group a
click is a message to the member who showed the card (`@Name ...`), in the open.

## Pictures

An agent chooses the address, so it is never loaded as written (`media.ts`):
- a workspace file (`sandbox:chart.png`) is served by the authenticated file endpoint;
- a small `data:` raster image is shown as it is;
- an `https://` picture goes through `GET /api/v1/media/image?url=` (server feature `conversations.pictures`): the server fetches it
  once, accepts only public addresses (every resolved address is checked and the connection is pinned to one, so DNS rebinding does
  not help), https on 443 without credentials, redirects re-checked, at most 5 MB, PNG/JPEG/GIF/WebP by header and first bytes
  (never SVG), and caches it. The person's browser never contacts the site;
- anything else is not shown. Video and audio come only from the workspace.

## One bubble per turn

A turn's tool cards are drawn once, in the turn's closing bubble, after its text.

## Groups

A member's post carries what its tool showed in `data.ui` (the channel entry's data; the latest surface of the turn). The group view
draws it folded to its title under the message, and draws a model's `a2ui` fence in the text the same way. Note: the open server's
group endpoints are a thin skeleton (edit, delete and reactions are not implemented, and the response shape is not the one the
console's group client reads); making groups work end to end is separate work.

## Teaching an agent to draw

Add `generative_ui` to the agent's tools. Its instructions then include `a2ui_prompt.md`, generated from the catalog:

```bash
pnpm --filter agents-console a2ui:prompt    # after changing the catalog or the rules in src/lib/a2ui/prompt.ts
```

A test fails when the committed prompt and the catalog differ. It costs about 5,000 tokens, so it is opt-in.

## Returning a card from a tool (Python)

`loamz-solutions` has a small builder, `kit/a2ui.py`:

```python
from loamz.solutions.kit.a2ui import Reply, button, card, metric, metrics, send, surface

ui = surface("plan-3f2a", card(metrics(metric("Equity", "$151,288")),
                               button("Approve plan 3f2a", send("Approve plan 3f2a"), "primary"), title="Plan 3f2a"))
return Reply.shown("Plan 3f2a: ...", ui)    # text for the model, card for the person
```

## What a card cannot do

No script, no HTML, no classes, no addresses loaded as written, no `openUrl`. A button only sends text. A card in an old message can
still be clicked: whatever it asks for goes through the agent's checks again (the trading desk refuses an approval for any plan that
is not the current one).
