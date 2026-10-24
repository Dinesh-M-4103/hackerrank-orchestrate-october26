# Token usage and cost - final full-dataset run

**The run that produced `output.csv` made no model call.** It is a deterministic local pipeline (Python standard library + `pypdf`
for PDF text): no provider, no API call, no runtime tokens. One *development-time* model use feeds it, disclosed below.

## Final run (`python code/main.py run`)

| item | value |
|---|---|
| requests processed | 518 |
| model providers | none |
| model names | none |
| model calls | **0** |
| input tokens | **0** |
| output tokens | **0** |
| total tokens | **0** |
| average tokens per request | **0** |
| estimated total cost | **$0.00** |
| estimated cost per request | **$0.00** |

Per-model totals: not applicable (no models). Overall total: 0 calls, 0 tokens, $0.00. The run is offline and needs no API key.

## What was *not* part of the final run (full disclosure)

* **One aborted Gemini attempt.** A feature-flagged screenshot interpreter exists in the code (`ghost_audit/vision/`, disabled by default).
  One real request was made to `gemini-2.5-flash` during development; the API rejected it (HTTP 404, model not available to the key). It
  returned no content, so the client recorded 0 tokens (a rejected request produces no billable output); the run stopped as designed
  (1 request of a 24-request cap), nothing was cached, and its output file contained only error records. Nothing from it is used anywhere in `output.csv`.
* **Screenshot transcription by the coding agent.** The 11 PNG screenshots were read by eye by the coding agent (Claude Code, model
  `claude-sonnet-5-5`) and written to `code/evaluation/screenshot_transcriptions.json`, which `python code/main.py run` loads (each entry is
  hash-checked against its PNG and validated like model output). That is model-based extraction done once during development, not a
  runtime call: re-running the pipeline makes no model call and needs no key. Its token usage is not measured here.
* **Development assistance.** The code was written with a coding agent (Claude Code); its usage is a development cost, not a runtime
  component, and is not measured here (the session trace is submitted separately as `agent-trace.zip`).
* **Unit tests** use a fake vision client and no network.

## Reproduce

`python code/main.py run` (offline). Cost and token counts above are 0 by construction: the pipeline contains no code path that
calls a model unless `GHOST_AUDIT_VISION=gemini`, a key and `--approve-real-calls` are all supplied to a different command (`vision-run`).
