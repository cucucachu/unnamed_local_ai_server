# tool-calling-spike

M1-03 risk-gate spike: validates native tool-calling reliability against the
real `model-runner` (llama.cpp + Gemma 4, `--jinja`) before `deepagents`
adoption in M2-03. See `spike.py` header for usage and `../../docs/TOOL_CALLING.md`
for results and verdict.

M8-06 risk-gate spike: re-validates the same 75-run tool-calling matrix
with Gemma 4 thinking re-enabled (`--reasoning-format deepseek`), plus two
comparison configurations (`enable_thinking=false` per-request,
`--reasoning-budget 1024`). Reuses `spike.py`'s case functions/tools
directly. See `spike_m8_06.py` header for usage,
`m8_06_results/*.json` for raw per-call data, and
`../../docs/TOOL_CALLING.md`'s M8-06 section for results and verdict.
