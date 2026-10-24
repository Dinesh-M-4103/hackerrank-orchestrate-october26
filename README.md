# HackerRank Orchestrate - October 2026

## Ghost Data Still Exists

Build an AI agent that audits whether a user's personal data was actually deleted.

A deletion workflow may report **Complete** while data still exists in databases, logs, support attachments, exports, search indexes, archives, or backups. Sync and restore processes may also bring deleted data back.

For each request, the agent must first determine whether deletion was actually requested. It then returns `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`, reports any remaining data or recreation risk, and produces the ordered remediation and verification steps required to finish an authorized deletion safely.

Read [`problem_statement.md`](./problem_statement.md) for the complete challenge contract.

---

## Quick Start

Clone the repository and move into the project directory:

```bash
git clone https://github.com/interviewstreet/hackerrank-orchestrate-october26.git
cd hackerrank-orchestrate-october26
```

Start your AI coding tool from the **repository root** so it automatically reads [`AGENTS.md`](./AGENTS.md) and logs your session turns to `log.txt` in the root directory.

Build your solution in `code/main.py`, or use another language and document its setup and run command clearly in your README.

Your solution must:

- Read the input files from `dataset/`
- Generate one prediction row for every request in `dataset/deletion_requests.csv`
- Write the final predictions to `output.csv` in the repository root

Run the starter Python entry point with:

```bash
python3 code/main.py
```

The blank template at `dataset/output.csv` is provided as a reference. Your final generated file for submission must be the root-level `output.csv`.


## Dataset

All participant-facing files are in `dataset/`:

| File | Contents |
| --- | --- |
| `deletion_requests.csv` | 518 requests |
| `sample_deletion_requests.csv` | 32 sample requests |
| `sample_output.csv` | Correct sample answers |
| `user_identities.csv` | Current user identities |
| `user_audit_log.csv` | Historical identity changes |
| `system_activity.csv` | Activity across services and data processes |
| `deletion_rules.csv` | Required treatment for each data category |
| `evidence_files.csv` | Manifest of supplied evidence files |
| `output.csv` | Blank output template |

Evidence is stored under `dataset/evidence/` and may include PDFs, images, logs, JSON, plain text, or SQL snapshot exports. SQL captures may contain a primary database, regional replicas, a reporting replica, or a failover replica so replication drift can be investigated directly. The `.sql` files are readable as text and executable with SQLite.

Only `deletion_requests.csv` requires answers. The other files provide evidence and context.

---

## Output

Create `output.csv` with these columns in this exact order:

```text
case_id,deletion_status,steps_json
```

Produce exactly one row per request. `deletion_status` is `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`. `steps_json` is an ordered JSON array containing findings, remediation actions, and verification checks. Use an empty array (`[]`) for `NOT_APPLICABLE`. Evidence references may be a `file_id`, `activity_id`, or `audit_id`. Determine the resource state at `audit_at` from the available evidence; reported workflow statuses and operation results are claims. Use `dataset/sample_output.csv` as the format reference.

The final file must contain 518 data rows sorted by `case_id` (plus the header row). The grader parses `steps_json` as JSON, so object-key order and whitespace do not matter; array order does. Order findings, remediation, and verification in that phase order. Within a phase, follow the operation order in the problem statement, then sort by `system_id` and `resource_id`.

---

## Evaluation

`output.csv` will be compared with hidden canonical answers. Scoring considers:

- deletion assessment accuracy;
- current and historical identity resolution;
- discovery of remaining data and recreation risks;
- correct policy application and evidence references; and
- completeness and ordering of remediation and verification steps.

Your `code.zip` must include:

```text
code/evaluation/evaluation_report.md
code/evaluation/usage_report.md
```

- `evaluation_report.md` must cover case, row, field, and sequence accuracy, at least three additional evaluation cases created by you, failures found, and improvements made.
- `usage_report.md` must cover the final full-dataset run, including model providers and names, model calls, input and output tokens, total and average tokens per request, and estimated total and per-request cost. Include per-model and overall totals when using multiple models.

**Important:** Place `evaluation_report.md` and `usage_report.md` inside `code/evaluation/` (matching `code/evaluation/main.py` in this starter layout), write your complete evaluation metrics, custom evaluation test cases, and token/cost breakdowns directly inside those Markdown files, and keep prompts in source code or Markdown files. Automated code evaluation ignores raw data files (`.csv`, `.tsv`, `.json`, `.jsonl`, `.sql`, `.log`), so content stored only in those formats will not be visible to the code judge or AI interviewer.

---

## Chat Transcript

This repository includes [`AGENTS.md`](./AGENTS.md), which instructs compatible AI coding tools to append conversation summaries to `log.txt` in the repository root:

| Platform | Path |
| --- | --- |
| macOS / Linux | `<repo root>/log.txt` |
| Windows | `<repo root>\log.txt` |

- Always launch your AI coding agent from the repository root (or explicitly ask it to read `./AGENTS.md` first) so every turn is logged to `log.txt` with the required `User Prompt`, `Agent Response Summary`, `Actions`, and `Context` sections.
- Upload `log.txt` directly as your `chat_transcript` submission on HackerRank.
- Do not include API keys, tokens, credentials, or secrets in prompts, code, configuration, or `log.txt`.

---

## Packaging `code.zip` and Submission

| Item | Description |
| --- | --- |
| `code.zip` | Runnable solution, prompts, configuration, `README.md`, and `evaluation/` reports |
| `output.csv` | One answer row for each of the 518 requests in `dataset/deletion_requests.csv` |
| `chat_transcript` | The root `log.txt` file generated by your coding agent |
| AI interview | Live explanation of your architecture, approach, and evaluation |

You may use any programming language, model, framework, or retrieval approach. Include clear setup and execution instructions in `README.md` inside `code.zip`.

To package `code.zip` from the repository root without bundling the dataset:

**macOS / Linux:**

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```

**Windows (PowerShell):**

```powershell
Compress-Archive -Path README.md, code -DestinationPath code.zip -Force
```

When packaging `code.zip`:

- Keep your evaluation reports in `code/evaluation/` so they are bundled automatically with `code` (or add `evaluation` to the archive command above if you placed them in a top-level `evaluation/` directory).
- Do **not** include `dataset/`, virtual environments (`.venv`, `venv`, `node_modules`), or build caches (`__pycache__`). On Windows, keep `.venv` at the repository root (outside `code/`) and remove any `code/__pycache__` folders before running `Compress-Archive`.
- Do **not** include nested archives (`.zip`, `.tar`, `.gz`, `.tgz`, `.7z`, `.rar`) inside `code.zip` — submissions containing nested archive files will be rejected.

Before submitting, confirm that:

- `output.csv` has the exact header `case_id,deletion_status,steps_json` and contains all 518 data rows sorted by `case_id`;
- every `steps_json` value is a valid JSON array with consecutive step numbers starting at `1`;
- findings cite valid evidence IDs, and `COMPLETE` verification steps cite evidence proving the current state;
- proposed remediation and future verification steps use an empty `evidence_id` (`""`);
- `code.zip` includes your runnable code, `README.md`, `code/evaluation/evaluation_report.md`, and `code/evaluation/usage_report.md`;
- `log.txt` follows the `AGENTS.md` turn format and is uploaded to the `chat_transcript` slot; and
- no API keys, credentials, real personal data, or sensitive configuration values are included.

---

## Environment Setup (this solution)

Requires Python 3.10+ (developed on 3.14). The pipeline uses the standard library plus `pypdf`; no API key is needed for local runs or tests.

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Tests and checks (all offline; no API key, no network). Run from the repository root with `.venv` active:

```bash
python -m unittest discover -s code           # 211 unit + dataset-integration tests
python code/main.py coverage                  # parse-coverage over every evidence file (images are listed as needing a vision model)
python code/main.py attribution               # account-attribution report for deletion_requests.csv (CONFIRMED / PROBABLE / AMBIGUOUS / NONE)
python code/main.py validate-output FILE      # validate a CSV against the official output schema (add --full to require all 518 cases)
python code/evaluation/main.py --self-check   # scorer sanity check against dataset/sample_output.csv
python code/evaluation/main.py --baseline all-incomplete    # naive baseline floor on the 32 samples
python code/evaluation/main.py --pred output.csv            # score a predictions CSV against the samples
```

Dataset-dependent tests are skipped automatically when `dataset/` is absent (it is not part of `code.zip`).

**Project status: complete deterministic submission.** `python code/main.py run` writes `output.csv` for all 518 requests using only
the local pipeline (no model, no network, no API key, strict audit cutoff) and validates it.

```bash
python code/main.py run                       # writes <repo root>/output.csv, then runs the independent checks
python code/main.py run --case-order numeric  # same, rows in numeric case order (case_33 before case_100); default is plain string order
python code/main.py check-output output.csv   # re-validate any output file against the dataset (schema, coverage, evidence IDs, cutoff)
python code/evaluation/main.py --custom       # the participant-written evaluation cases
python code/evaluation/make_reports.py        # regenerate code/evaluation/evaluation_report.md and usage_report.md from live measurements
python code/main.py package                   # build and verify code.zip (README.md, requirements*.txt, code/; never dataset/, .env, caches, archives)
python code/main.py package-trace --export <your /export file>   # after ending the session: assemble agent-trace.zip from the REAL session files
python code/main.py verify-trace              # verify agent-trace.zip (JSONL validity, secrets, staleness vs the live session file)
python code/main.py predict --samples --mismatches    # plan and score the 32 public samples, explaining every difference
python code/main.py ambiguity-report          # aggregate: do ambiguous account candidates share a state?
```

Submission files: `output.csv` (repository root), `code.zip` (from `package`), `log.txt` (the chat transcript), and `agent-trace.zip`
(from `package-trace`, per `agent_trace_instructions.md`). The reports are in `code/evaluation/`.

**Row order.** The specification says rows are "sorted by `case_id`" without saying string or numeric order. Every organizer-produced file is in
numeric order and IDs are zero-padded to two digits then unpadded, so `case_01`..`case_32` read the same either way but `case_100` sorts before
`case_33` as a string. The checker accepts either order unless `--case-order` is given.

### Rules the pipeline follows (and where the specification is silent)

* **Admissibility.** A fact is usable for a case only if its *file* was captured at or before `audit_at` and its inner
  record/line time (if any) is also at or before `audit_at`. A file captured after `audit_at` is inadmissible in full,
  even if it contains an earlier-dated snapshot (strict reading of "never cite evidence captured after audit_at").
  `StateConfig(admissibility="record")` trusts inner record times; it is a diagnostic only and is never used to cite.
* **Ownership cutoff.** Default `strict`: a resource is tied to a person only by evidence/links captured at or before
  `audit_at`. `link_only` (diagnostic, opt-in) lets later evidence reveal *who owns* a resource, never its state.
* **State.** Latest admissible observation wins; observations older than 30 days before `audit_at` are stale; same-time
  disagreement is a conflict resolved to the least compliant reading; a record on any replica node survives; an
  enumerated, admissible, fresh snapshot that omits a resource proves absence only within its own system/collection
  (JSON bundles only for subjects present in the file; retained categories never by silence). `reported_status` and
  activity results are claims and never decide a state.
* **Policy** is read from `deletion_rules.csv`; data flows (no CSV row) must be disabled; backups are retained with
  restore blocked; tombstones keep only the stable reference.
* **Not resolved by the specification (no steps are invented, a note is recorded):** a retained record that is missing, a
  REDACT-category record that was deleted outright, an expired retention/legal-hold period, support messages whose PII
  status cannot be told, whether support conversations always contain shared context, and what to do with an account that
  cannot be attributed.

### Screenshot evidence (optional, Gemini behind a feature flag)

Eleven evidence files are PNG console screenshots that need a vision model. The integration is **off by default** and never
required: unit tests, the deterministic pipeline and the fake client use no key and no network.

```bash
python code/main.py vision-preflight                      # offline: what would be sent, token/cost estimate, config, SDK/key presence
python code/main.py vision-inspect --fake code/evaluation/screenshot_transcriptions.json   # fake client, no network
python code/main.py vision-compare --fake code/evaluation/screenshot_transcriptions.json   # pipeline disabled vs fake-enabled
# real calls (NOT run by default; need explicit approval): all three opt-ins are required
#   python -m pip install -r requirements-gemini.txt
#   export GHOST_AUDIT_VISION=gemini GEMINI_API_KEY=...     # key from the environment only; never printed or logged
#   python code/main.py vision-run --approve-real-calls --out vision_interpretations.json
```

Contract: a vision step only *transcribes* what is visible (text, identifiers, displayed times, rows, possible links). It never supplies
evidence IDs, states, policy actions or step order; provenance comes from the manifest. A link is *supported* only when the resource and the
subject are visibly in the same row/panel and the quote contains both; filenames, headers and proximity are hypotheses, and a screen that
shows no resource ID (or only a file name) establishes nothing. A screenshot may be used only if its **manifest capture time** is at or
before `audit_at`; a *row* that itself displays a later time is excluded for earlier audits (a page that displays times later than its
manifest time is flagged, not discarded). Screens that list many unrelated events (audit explorers) only contribute rows that a catalog
link row also names. A screenshot that contradicts an existing link (shows a different, unresolvable subject for the same resource)
*disputes* it and the resource is not asserted.

**What the submitted run uses.** The real Gemini attempt failed (HTTP 404), so `python code/main.py run` (default `--screenshots transcribed`)
reads `code/evaluation/screenshot_transcriptions.json`: a transcription of the 11 PNGs made by the coding agent (Claude Code, claude-sonnet-5-5)
reading them by eye during development. Each entry stores the SHA-256 of its PNG (a mismatch is refused) and goes through the same validator as
a model response. `--screenshots none` ignores screenshots entirely. See `code/evaluation/screenshot_inspection.md` and the limits stated in
`code/evaluation/evaluation_report.md`.

### Code layout (`code/ghost_audit/`)

| Module | Role |
| --- | --- |
| `loaders.py`, `models.py`, `paths.py`, `timeutil.py` | CSV loading, data classes, repo-relative paths, UTC timestamps |
| `extractors/` | One extractor per evidence format (handover text, JSON, SQL snapshots, six log dialects, PDF via `pypdf`) producing normalized `Fact`s and `Scope`s |
| `text_ids.py`, `identity.py` | Exact-token, case-insensitive identifier matching; current + historical identity resolution |
| `owners.py` | Evidence-confirmed resource-owner map and explicit `LINK_RESOURCE_IDENTITY` links, with audit-cutoff-aware queries |
| `attribution.py` | Account attribution (confirmed / probable / ambiguous); never chooses among ambiguous candidates |
| `intent_hints.py` | Corroborating non-deletion signals (not the final NOT_APPLICABLE decision) |
| `intent.py` | Deterministic multilingual classifier: deletion vs access/export, correction, login help, marketing opt-out |
| `observations.py`, `state.py` | Normalize facts into resource-state observations; reconstruct each resource's state at `audit_at` |
| `policy.py` | `deletion_rules.csv` as explicit, testable logic (assessments, final-state verification) |
| `planner.py`, `pipeline.py`, `report.py` | Ordered findings / remediation / verification, status decision, orchestration, mismatch and ambiguity reports |
| `coverage.py`, `schema.py`, `scoring.py`, `context.py` | Parse-coverage check, official output-schema validator, local scorer and naive baselines, context assembly |

**Optional Gemini integration.** Disabled by default. If enabled later, set `GEMINI_API_KEY` in the environment (or an uncommitted `.env`) and install the optional package listed in `requirements.txt`. Keys are never stored in the repository.

`.venv/`, `.env`, caches, `code.zip` and `agent-trace*` are git-ignored.
