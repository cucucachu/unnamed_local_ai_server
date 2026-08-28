# M2-06 — Chat screen (stream rendering, tool status)

**Milestone**: M2 · **Size**: L · **Depends on**: M2-04, M2-05 · **Blocks**: M2-07, M3-04, M4-06

## Context

The core UX: a streaming chat view rendering tokens live and tool activity inline. Built
entirely against the typed frames from `lib/chatSocket.ts` (M2-05); works identically on web
and native by construction (plain RN components, no web-only APIs).

## Spec

1. **State model** (`lib/useChat.ts` hook, React state only — no external state lib):

```ts
type ChatItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string; streaming: boolean }
  | { kind: "tool"; name: string; category: "file"|"exec"|"plan"|"other";
      status: "running"|"success"|"error"; args: object; resultPreview?: string };
```

   - `useChat(threadId)` returns `{ items, sendMessage, busy, connectionState }`.
   - Frame handling: `turn_start` → append streaming assistant item (empty). `token` → append
     to the current streaming item. `tool_start` → append running tool item (tokens after it go
     to a NEW streaming assistant item — tool items visually split the response). `tool_end` →
     mark matching tool item by `tool_call_id`. `turn_end` → mark streaming false, `busy` false.
     `error` → append an error-styled assistant item, `busy` false.
   - For v1 the thread id is fixed: `"default"` (thread switching arrives in M3-04). Keep the
     hook signature ready.
2. **`app/(tabs)/chat.tsx`**:
   - Inverted `FlatList` of items; user bubbles right-aligned, assistant left, max width 85%.
   - Tool items: compact card — icon by category (file: document icon, exec: terminal icon,
     plan: list icon), tool name, spinner while running, tap to expand a monospace block with
     args + result preview (collapsed by default).
   - Composer: multiline `TextInput` + send button pinned above the keyboard
     (`KeyboardAvoidingView`); send disabled while `busy` or when empty; Enter sends on web,
     newline on native (Platform check).
   - Auto-scroll to newest on new items; connection state pill ("connecting…/reconnecting…")
     when not open.
   - Streaming cursor: a `▍` suffix on the streaming assistant item.
3. Styling: keep to the app's dark theme constants in `lib/theme.ts` (create: bg `#0e1116`,
   surface `#161b22`, accent `#4f8cff`, text `#e6edf3`, mono font for tool blocks).
4. **Tests** (jest): `useChat` reducer logic driven by synthetic frame sequences — the five
   sequences mirroring M2-04's test cases; assert resulting `items` shapes (no rendering
   assertions needed beyond one shallow render of the screen).

## Out of scope

Thread list/switching (M3-04), history hydration (M3-04), markdown rendering of assistant text
(plain text v1 — explicitly), stop button, message editing.

## Acceptance criteria (Tier A)

- [ ] `npm test` + `npx tsc --noEmit` green.
- [ ] Full-stack smoke on host (real model): rebuild caddy, open the stack; run
      `scripts/e2e/chat_browser_smoke.sh` (write it: uses `docker run --rm --network host` with
      a playwright image or `npx playwright` locally — navigate `http://localhost/`, go to Chat,
      type "Say exactly: PONG", assert an assistant bubble appears containing text within 120 s).
      Script committed and exits 0.
- [ ] The chat screen imports frame types only from `lib/chatSocket.ts` (grep — no ad-hoc frame
      parsing in components).

## Tier B (append to docs/HOST-CHECKS.md under M2)

- [ ] Phone browser: send a message, watch tokens stream live.
- [ ] Expo Go: same, confirming keyboard behavior and send button.
