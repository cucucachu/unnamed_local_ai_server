# Integration call-style eval (M18-09, #341 experiment 4)

How well does the local model use an integration through the fixed tools we
ship (`open_integration`, then `use_integration`), compared with binding the
integration's tools natively? And what does each style cost the prompt cache
when an integration is opened mid-chat?

Nine tasks run against a simulated house (four lights on two floors, a TV, a
shopping list), eight of them for Home Assistant:

- turn a lamp on;
- a question about a light;
- dim a light;
- a whole floor off;
- add to the shopping list;
- TV volume;
- an out-of-range brightness (150%), which tests recovery from `invalid_arguments`;
- a two-part request;
- and one for HomeAI itself (save a note in personal files), which must leave
  the house alone.

The user has no apps or files yet: `list_apps`, `ls` and `glob` answer that way,
`write_file` records the file, and other HomeAI tools answer that they aren't
available.

The tool definitions are Home Assistant's own (`ha_tools.json`, as its MCP
Server integration lists them; generic, nothing specific to a household). Every
case starts from the agent's real first request: its tools and system prompt,
captured by running this tree's agent-server against its fake model
(`capture_prefix.py`).

- **fixed**: what ships. `open_integration` returns the schemas as text;
  `use_integration` calls a tool. Arguments are checked against the schema the
  way the gateway checks them, and results and errors are worded the way the
  agent's tools word them. The integrations list is the agent's own: each
  integration's tool names, and a line saying requests about an integration's
  devices, lists or data go to the integration rather than HomeAI's own apps.
- **fixed-bare**: fixed, with the list as it was before this eval: no tool
  names, no routing line.
- **native**: Home Assistant's tools bound directly as `ha_*` tools, and the
  integration tools left out. The list is the bare one, naming the `ha_*`
  tools.

A case passes on the house's end state, and for the question, on the reply.
Each case runs `EVAL_REPEATS` times per style (default 3). The report counts
passes, `invalid_arguments` (and recoveries), model calls, wall time and prompt
tokens processed.

The **prefix probe** then measures how many prompt tokens llama.cpp has to
process on the request right after opening the integration:

- with fixed, the schemas arrive as a tool result at the end;
- with native binding mid-chat, the new tools change the tool list, which the
  chat template renders before the system prompt.

## Run

Needs the live stack (model-runner and agent-server). It talks to model-runner
directly from a throwaway container on homeai-internal and changes nothing
else: no users, no integrations. It takes `/tmp/homeai-stack.lock` itself.

```bash
scripts/eval/integrations/run.sh
EVAL_STYLES=fixed,fixed-bare,native scripts/eval/integrations/run.sh
EVAL_ONLY=over-range EVAL_REPEATS=1 scripts/eval/integrations/run.sh
```

Report: `scripts/eval/integrations/last-report.json` (gitignored, as is the
captured `prefix.json`).
