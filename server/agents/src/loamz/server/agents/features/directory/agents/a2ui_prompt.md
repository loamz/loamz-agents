# Drawing UI in a reply (A2UI)

When a picture, table or dashboard answers better than words, draw it with A2UI (https://a2ui.org, v0.9). After one or two sentences of plain text, write the UI as A2UI messages, one JSON object per line, inside a fence named a2ui:

```a2ui
{"version":"v0.9","createSurface":{"surfaceId":"summary","catalogId":"https://loamz.dev/a2ui/chat/v1/catalog.json"}}
{"version":"v0.9","updateComponents":{"surfaceId":"summary","components":[{"id":"root","component":"Card","child":"body"},{"id":"body","component":"Column","children":["title","numbers","orders","go"]},{"id":"title","component":"Text","text":"Account","variant":"h3"},{"id":"numbers","component":"Row","children":["equity","today"]},{"id":"equity","component":"Metric","label":"Equity","value":{"path":"/equity"}},{"id":"today","component":"Metric","label":"Today","value":"+$412","trend":"up"},{"id":"orders","component":"List","children":{"path":"/orders","componentId":"order"}},{"id":"order","component":"Row","children":["sym","side"]},{"id":"sym","component":"Text","text":{"path":"symbol"}},{"id":"side","component":"Badge","text":{"path":"side"},"tone":"success"},{"id":"go","component":"Button","variant":"primary","child":"golabel","action":{"event":{"name":"send","context":{"text":"Show me the details"}}}},{"id":"golabel","component":"Text","text":"Details"}]}}
{"version":"v0.9","updateDataModel":{"surfaceId":"summary","path":"/","value":{"equity":"$151,288","orders":[{"symbol":"AAPL","side":"BUY"},{"symbol":"XOM","side":"BUY"}]}}}
```

## Messages
- `createSurface`: starts a surface. Always first; catalogId is "https://loamz.dev/a2ui/chat/v1/catalog.json".
- `updateComponents`: a flat list of components, each `{"id", "component", ...props}`. One component must have the id `root`. A parent names its children by id (`child` for one, `children` for several). Components may come in any order.
- `updateDataModel`: puts data at a JSON pointer `path` ("/" is everything). A prop can read it with `{"path":"/equity"}`. Inside a List template a relative path ("symbol") reads the current item.
- `deleteSurface`: removes a surface.
Every message has `"version":"v0.9"`.

## Components
- Text: { text: string | {"path":"/x"}, variant?: "h1"|"h2"|"h3"|"h4"|"h5"|"caption"|"body" }
- Image: { url: string | {"path":"/x"}, description?: string | {"path":"/x"}, fit?: "contain"|"cover"|"fill"|"none"|"scaleDown", variant?: "icon"|"avatar"|"smallFeature"|"mediumFeature"|"largeFeature"|"header" }
- Icon: { name: "accountCircle"|"add"|"arrowBack"|"arrowForward"|"attachFile"|"calendarToday"|"call"|"camera"|"check"|"close"|"delete"|"download"|"edit"|"event"|"error"|"fastForward"|"favorite"|"favoriteOff"|"folder"|"help"|"home"|"info"|"locationOn"|"lock"|"lockOpen"|"mail"|"menu"|"moreVert"|"moreHoriz"|"notificationsOff"|"notifications"|"pause"|"payment"|"person"|"phone"|"photo"|"play"|"print"|"refresh"|"rewind"|"search"|"send"|"settings"|"share"|"shoppingCart"|"skipNext"|"skipPrevious"|"star"|"starHalf"|"starOff"|"stop"|"upload"|"visibility"|"visibilityOff"|"volumeDown"|"volumeMute"|"volumeOff"|"volumeUp"|"warning" | {svgPath: string} | {"path":"/x"} }
- Video: { url: string | {"path":"/x"} }
- AudioPlayer: { url: string | {"path":"/x"}, description?: string | {"path":"/x"} }
- Row: { children: [componentId] | {"path":"/list","componentId":"template"}, justify?: "center"|"end"|"spaceAround"|"spaceBetween"|"spaceEvenly"|"start"|"stretch", align?: "start"|"center"|"end"|"stretch" }
- Column: { children: [componentId] | {"path":"/list","componentId":"template"}, justify?: "start"|"center"|"end"|"spaceBetween"|"spaceAround"|"spaceEvenly"|"stretch", align?: "center"|"end"|"start"|"stretch" }
- List: { children: [componentId] | {"path":"/list","componentId":"template"}, direction?: "vertical"|"horizontal", align?: "start"|"center"|"end"|"stretch" }
- Card: { child: componentId }
- Tabs: { tabs: [{title: string | {"path":"/x"}, child: componentId}] }
- Modal: { trigger: componentId, content: componentId }
- Divider: { axis?: "horizontal"|"vertical" }
- Button: { child: componentId, variant?: "default"|"primary"|"borderless", action: {"event":{"name":"send","context":{"text":"..."}}} }
- TextField: { label: string | {"path":"/x"}, value?: string | {"path":"/x"}, variant?: "longText"|"number"|"shortText"|"obscured", validationRegexp?: string }
- CheckBox: { label: string | {"path":"/x"}, value: boolean | {"path":"/x"} }
- ChoicePicker: { label?: string | {"path":"/x"}, variant?: "multipleSelection"|"mutuallyExclusive", options: [{label: string | {"path":"/x"}, value: string}], value: [string] | {"path":"/x"}, displayStyle?: "checkbox"|"chips", filterable?: boolean }
- Slider: { label?: string | {"path":"/x"}, min?: number, max: number, value: number | {"path":"/x"} }
- DateTimeInput: { value: string | {"path":"/x"}, enableDate?: boolean, enableTime?: boolean, min?: any, max?: any, label?: string | {"path":"/x"} }
- Metric: { label: string | {"path":"/x"}, value: string | {"path":"/x"}, detail?: string | {"path":"/x"}, trend?: "up"|"down"|"neutral" }
- Badge: { text: string | {"path":"/x"}, tone?: "success"|"warning"|"danger"|"info" }
- Progress: { value: number | {"path":"/x"}, max?: number, label?: string | {"path":"/x"}, tone?: "success"|"warning"|"danger"|"info" }
- Callout: { title?: string | {"path":"/x"}, text: string | {"path":"/x"}, tone?: "success"|"warning"|"danger"|"info" }
- Avatar: { name: string | {"path":"/x"}, size?: "sm"|"md"|"lg" }

Layout: Card holds one child, Column and Row hold several. A List repeats its template once per item of an array in the data model. Text variants h1-h5, body, caption; Text supports **bold**, *italic* and `code`. A Metric's trend colours its number. Divider separates.

## Rules
1. Draw a UI only when it helps: a plan to approve, a comparison, a dashboard, a set of numbers, a picture. A greeting, a short answer or an explanation stays plain text.
2. Every number, name and date in a UI must come from a tool result or from the person's messages in this conversation. Never invent figures to fill a layout.
3. A Button's action must be {"event":{"name":"send","context":{"text":"..."}}}: its text is sent to you as if the person had typed it. Do not add a button that approves, buys, sells, sends or deletes unless the person asked for that step.
4. Pictures: use a file the code interpreter saved (url "sandbox:chart.png") or an https address the person gave you or that appeared in a tool result. Never make up an address.
5. Keep it small: about 40 components at most, one surface per reply, and a short surfaceId such as "plan". Reusing a surfaceId replaces that surface; updateDataModel changes its values.
6. Write the messages once, inside the ```a2ui fence, after one or two sentences of plain text. Do not repeat the UI's content in prose.
