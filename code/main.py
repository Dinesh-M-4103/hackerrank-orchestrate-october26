"""Ghost Data Still Exists - command line entry point.

    python code/main.py coverage                 # parse-coverage check over all evidence files
    python code/main.py attribution              # account-attribution report over deletion_requests.csv
    python code/main.py validate-output FILE     # validate an output CSV against the official schema
    python code/main.py evaluate --pred FILE     # score predictions against the samples (see code/evaluation/main.py)
    python code/main.py run                      # write <repo root>/output.csv for all requests, then validate it
    python code/main.py check-output FILE        # independent validation: schema, coverage, evidence IDs, audit cutoff
    python code/main.py package                  # build and verify code.zip
    python code/main.py package-trace --export FILE   # assemble agent-trace.zip from the real session files + your /export
    python code/main.py verify-trace             # verify agent-trace.zip (secrets, JSONL, staleness)
    python code/main.py predict --samples [--mismatches] [--ownership strict|link_only]   # score the 32 samples
    python code/main.py ambiguity-report         # aggregate effect of ambiguous accounts (no per-case output)

``run`` writes the submission ``output.csv`` (deterministic, strict audit cutoff, no model calls, no network, no API key).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost_audit.paths import DATASET_DIR, REPO_ROOT  # noqa: E402


def cmd_coverage(args) -> int:
    from ghost_audit.context import build_context
    from ghost_audit.coverage import coverage_for

    ctx = build_context(args.dataset)
    rep = coverage_for(ctx.manifest, ctx.extractions)
    print(rep.summary())
    return 0 if rep.passed else 1


def cmd_attribution(args) -> int:
    from ghost_audit.attribution import AccountAttributor, summarize
    from ghost_audit.context import build_context
    from ghost_audit.loaders import load_requests

    ctx = build_context(args.dataset)
    cases = load_requests(args.dataset, sample=args.samples)
    attributor = AccountAttributor(ctx.resolver, ctx.owners, ctx.activities, allow_elimination=not args.no_elimination)
    results = attributor.attribute(cases, strict_cutoff=not args.no_cutoff)
    print(json.dumps(summarize(results), indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps({k: v.to_dict() for k, v in sorted(results.items())}, indent=1), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


def cmd_validate(args) -> int:
    from ghost_audit.loaders import load_requests
    from ghost_audit.schema import validate_csv

    expected = [c.case_id for c in load_requests(args.dataset)] if args.full else []
    problems = validate_csv(Path(args.file), expected)
    for p in problems[:50]:
        print(p)
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_evaluate(args) -> int:
    from evaluation.main import main as eval_main

    return eval_main(args.rest)


def cmd_predict(args) -> int:
    """Milestone 2: predictions for the 32 public samples only (they are attributed together with the evaluation requests
    because shared timing cohorts matter, but nothing for the evaluation requests is written or printed per case)."""
    from ghost_audit.loaders import load_requests
    from ghost_audit.pipeline import Pipeline, rows_for
    from ghost_audit.report import format_mismatches, mismatches
    from ghost_audit.scoring import read_rows, score

    if not args.samples:
        print("full-dataset predictions are not enabled in milestone 2; use --samples", file=sys.stderr)
        return 2
    out = Path(args.out).resolve() if args.out else None
    if out is not None and out == (REPO_ROOT / "output.csv").resolve():
        print("refusing to write the submission file output.csv during milestone 2", file=sys.stderr)
        return 2
    samples = load_requests(args.dataset, sample=True)
    pipe = Pipeline(ownership=args.ownership)
    plans = pipe.run(samples + load_requests(args.dataset))
    ids = {c.case_id for c in samples}
    plans = {k: v for k, v in plans.items() if k in ids}
    pred = rows_for(plans)
    truth = read_rows(Path(args.dataset) / "sample_output.csv")
    print(score(pred, truth).to_markdown())
    mm = mismatches(plans, pred, truth)
    print(f"\ncases with any difference: {len(mm)} of {len(truth)}")
    if args.mismatches:
        print(format_mismatches(mm))
    if out is not None:
        import csv
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["case_id", "deletion_status", "steps_json"])
            w.writeheader()
            for cid in sorted(plans):
                w.writerow(plans[cid].to_row())
        print(f"wrote {out}")
    return 0


def cmd_ambiguity(args) -> int:
    """Aggregate-only report on how ambiguous account candidates affect status for the evaluation requests."""
    from ghost_audit.loaders import load_requests
    from ghost_audit.pipeline import Pipeline
    from ghost_audit.report import ambiguity_effect

    plans = Pipeline().run(load_requests(args.dataset))
    print(json.dumps(ambiguity_effect(plans), indent=2))
    return 0


def _images(dataset):
    from ghost_audit.extractors import IMAGE_SUFFIXES
    from ghost_audit.loaders import load_manifest

    manifest = load_manifest(dataset)
    return manifest, [m for m in manifest if m.suffix in IMAGE_SUFFIXES]


def cmd_vision_preflight(args) -> int:
    """Everything that must be reviewed before the first real Gemini request. Offline: no network, no key needed."""
    import importlib.util
    import os

    from ghost_audit.vision.config import ENABLE_ENV, KEY_ENV, MODEL_ENV, PRICING_AS_OF, PRICING_SOURCE, VisionConfig
    from ghost_audit.vision.estimate import describe, estimate

    _, imgs = _images(args.dataset)
    cfg = VisionConfig.from_env()
    model = args.model or cfg.model
    est = estimate([m.path for m in imgs], model, cfg.max_attempts)
    print("== screenshots that would be sent (one request each) ==")
    for m, p in zip(imgs, est.per_image):
        print(f"  {m.file_id}  {m.rel_path}  captured {m.captured_at}  {p['width']}x{p['height']}  ~{p['input_tokens']:,} input tokens")
    print("\n== estimate ==\n" + describe(est))
    print("\n== what is sent ==\n  per request: ONE image + a fixed instruction prompt + a JSON response schema. Nothing else: no case IDs, request\n"
          "  text, identity records, other evidence or dataset rows. The screenshots themselves show fictional names, e-mails, phone\n"
          "  numbers and resource IDs (and in a few images other fictional customers).")
    print("\n== configuration ==")
    print(f"  {ENABLE_ENV} = {'gemini (enabled)' if cfg.enabled else 'not set (vision DISABLED)'}")
    print(f"  {KEY_ENV} set: {cfg.has_key()}   (value is never printed)")
    print(f"  {MODEL_ENV} = {os.environ.get(MODEL_ENV, '(default) ' + cfg.model)}")
    print(f"  google-genai SDK importable: {importlib.util.find_spec('google.genai') is not None}")
    print(f"  timeout {cfg.timeout_s:g}s; up to {cfg.max_attempts} attempts per image with exponential backoff ({cfg.base_delay_s:g}s..{cfg.max_delay_s:g}s) on "
          f"429/5xx/timeouts only; >= {cfg.min_interval_s:g}s between requests; hard cap {cfg.max_requests} requests per run")
    print(f"  real calls need ALL of: {ENABLE_ENV}=gemini, {KEY_ENV} in the environment, and --approve-real-calls on `vision-run`")
    print(f"\nprices: {PRICING_SOURCE} as of {PRICING_AS_OF}")
    return 0


def cmd_vision_inspect(args) -> int:
    """Interpret the screenshots with a FAKE client (hand-transcribed fixture) and show what would be extracted. Offline."""
    from ghost_audit.vision.client import UsageTracker
    from ghost_audit.vision.config import VisionConfig
    from ghost_audit.vision.facts import to_extraction
    from ghost_audit.vision.fixtures import fake_client_from_fixture
    from ghost_audit.vision.interpreter import ScreenshotInterpreter

    manifest, imgs = _images(args.dataset)
    cfg = VisionConfig(enabled=True, min_interval_s=0.0)
    interp = ScreenshotInterpreter(fake_client_from_fixture(Path(args.fake), manifest), cfg, UsageTracker(), sleep=lambda s: None)
    for m in imgs:
        it = interp.interpret(m)
        ex = to_extraction(m, it)
        print(f"{m.file_id} {m.rel_path.rsplit('/', 1)[-1]} [{it.status}] captured {m.captured_at} conf={it.confidence:.2f}")
        for l in it.links:
            print(f"    link {'SUPPORTED' if l.supported else 'hypothesis'}: {l.resource_id} ~ {l.subject} ({l.basis})" + ("" if l.supported else f" - {l.why_not}"))
        for f in ex.facts:
            print(f"    fact {f.resource_id} ids={sorted(f.identifiers.emails | f.identifiers.user_refs)} observed_at={f.observed_at}")
        for w in ex.warnings:
            print(f"    warning: {w}")
        for p in it.problems:
            print(f"    problem: {p}")
    return 0


def cmd_vision_compare(args) -> int:
    from ghost_audit.loaders import load_manifest
    from ghost_audit.vision.client import UsageTracker
    from ghost_audit.vision.compare import compare, format_comparison
    from ghost_audit.vision.config import VisionConfig
    from ghost_audit.vision.fixtures import fake_client_from_fixture
    from ghost_audit.vision.interpreter import ScreenshotInterpreter

    manifest, _ = _images(args.dataset)
    interp = ScreenshotInterpreter(fake_client_from_fixture(Path(args.fake), manifest), VisionConfig(enabled=True, min_interval_s=0.0),
                                   UsageTracker(), sleep=lambda s: None)
    print(format_comparison(compare(interp.interpret_all(manifest), args.dataset)))
    return 0


def cmd_vision_run(args) -> int:
    """The ONLY command that can contact Gemini. Refuses unless every opt-in is present; writes raw interpretations to --out."""
    from ghost_audit.vision.client import UsageTracker, VisionDisabled, build_real_client
    from ghost_audit.vision.config import VisionConfig
    from ghost_audit.vision.interpreter import ScreenshotInterpreter

    manifest, _ = _images(args.dataset)
    cfg = VisionConfig.from_env(cache_dir=args.cache_dir)
    try:
        client = build_real_client(cfg, args.approve_real_calls)
    except VisionDisabled as exc:
        print(str(exc), file=sys.stderr)
        return 2
    usage = UsageTracker()
    results = ScreenshotInterpreter(client, cfg, usage).interpret_all(manifest)
    out = {"model": cfg.model, "usage": usage.totals(), "interpretations": {k: v.to_dict() for k, v in results.items()}}
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(usage.totals()))
    return 0


def _live_key():
    import os
    import re
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        m = re.search(r"(?m)^\s*(?:export\s+)?GEMINI_API_KEY\s*=\s*(.*?)\s*$", env_file.read_text(encoding="utf-8"))
        if m:
            return m.group(1).strip().strip("\"'")
    return os.environ.get("GEMINI_API_KEY")


def cmd_package_trace(args) -> int:
    """Assemble agent-trace.zip from the REAL Claude Code session files for this repo plus the /export transcript(s) you give it."""
    from ghost_audit.packaging import build_trace_zip, find_claude_sessions, verify_trace_zip

    sessions = find_claude_sessions(REPO_ROOT)
    exports = [Path(e).resolve() for e in (args.export or [])]
    for e in exports:
        if not e.is_file():
            print(f"export file not found: {e}", file=sys.stderr)
            return 2
    out = Path(args.out).resolve() if args.out else (REPO_ROOT / "agent-trace.zip")
    print(f"found {len(sessions)} session file(s): " + ", ".join(f"{s.name} ({s.stat().st_size / 1e6:.1f} MB)" for s in sessions))
    try:
        names = build_trace_zip(out, sessions, exports, _live_key())
    except ValueError as exc:
        print("REFUSED:", exc, file=sys.stderr)
        return 1
    problems = verify_trace_zip(out, _live_key(), sessions)
    print(f"wrote {out} with {len(names)} file(s)")
    for p in problems:
        print("PROBLEM", p)
    print("TRACE " + ("VERIFIED" if not problems else "NOT VERIFIED"))
    return 0 if not problems else 1


def cmd_verify_trace(args) -> int:
    from ghost_audit.packaging import find_claude_sessions, verify_trace_zip

    path = Path(args.file).resolve() if args.file else (REPO_ROOT / "agent-trace.zip")
    problems = verify_trace_zip(path, _live_key(), find_claude_sessions(REPO_ROOT))
    for p in problems:
        print("PROBLEM", p)
    print("TRACE " + ("VERIFIED" if not problems else "NOT VERIFIED"))
    return 0 if not problems else 1


def cmd_run(args) -> int:
    """Produce output.csv for every request in deletion_requests.csv (deterministic, strict cutoff, no model calls), then validate."""
    from ghost_audit.submission import check_output, run_pipeline, write_output

    out = Path(args.out).resolve() if args.out else (REPO_ROOT / "output.csv")
    plans = run_pipeline(args.dataset, screenshots=args.screenshots)
    write_output(plans, out, args.case_order)
    print(f"wrote {out} ({len(plans)} rows)")
    rep = check_output(out, args.dataset, args.case_order)
    print(rep.to_markdown())
    print("\nstatuses:", rep.stats["statuses"], "| steps:", rep.stats["steps_total"], "| citations:", rep.stats["citations"])
    print("VALIDATION " + ("PASSED" if rep.passed else "FAILED"))
    return 0 if rep.passed else 1


def cmd_check_output(args) -> int:
    from ghost_audit.submission import check_output

    rep = check_output(args.file, args.dataset, args.case_order)
    print(rep.to_markdown())
    print("VALIDATION " + ("PASSED" if rep.passed else "FAILED"))
    return 0 if rep.passed else 1


def cmd_package(args) -> int:
    """Build code.zip per the official instructions and verify it (never includes dataset/, .env, caches, archives)."""
    import os

    from ghost_audit.packaging import build_code_zip, verify_zip

    out = Path(args.out).resolve() if args.out else (REPO_ROOT / "code.zip")
    names = build_code_zip(REPO_ROOT, out)
    secret = None
    env_file = REPO_ROOT / ".env"
    if env_file.exists():                                  # the live key (if any) must not be inside the archive; value is never printed
        import re
        m = re.search(r"(?m)^\s*(?:export\s+)?GEMINI_API_KEY\s*=\s*(.*?)\s*$", env_file.read_text(encoding="utf-8"))
        if m:
            secret = m.group(1).strip().strip("\"'")
    problems = verify_zip(out, secret or os.environ.get("GEMINI_API_KEY"))
    print(f"wrote {out} ({len(names)} files, {out.stat().st_size:,} bytes)")
    for p in problems:
        print("PROBLEM", p)
    print("PACKAGE " + ("PASSED" if not problems else "FAILED"))
    return 0 if not problems else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(DATASET_DIR))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("coverage").set_defaults(fn=cmd_coverage)
    a = sub.add_parser("attribution")
    a.add_argument("--samples", action="store_true", help="use sample_deletion_requests.csv")
    a.add_argument("--no-elimination", action="store_true", help="disable joint elimination between cases")
    a.add_argument("--no-cutoff", action="store_true", help="DIAGNOSTIC ONLY: also use ownership evidence captured after audit_at")
    a.add_argument("--json", help="write per-case attribution detail to this path")
    a.set_defaults(fn=cmd_attribution)
    v = sub.add_parser("validate-output")
    v.add_argument("file")
    v.add_argument("--full", action="store_true", help="also require exactly the case_ids in deletion_requests.csv")
    v.set_defaults(fn=cmd_validate)
    e = sub.add_parser("evaluate")
    e.add_argument("rest", nargs=argparse.REMAINDER)
    e.set_defaults(fn=cmd_evaluate)
    pr = sub.add_parser("predict", help="predictions for the 32 public samples (milestone 2)")
    pr.add_argument("--samples", action="store_true", help="required: score the public samples")
    pr.add_argument("--ownership", choices=["strict", "link_only"], default="strict")
    pr.add_argument("--mismatches", action="store_true", help="print per-case mismatches and causes")
    pr.add_argument("--out", help="write the sample predictions to this CSV (never the repository-root output.csv)")
    pr.set_defaults(fn=cmd_predict)
    sub.add_parser("ambiguity-report", help="aggregate effect of ambiguous accounts on status").set_defaults(fn=cmd_ambiguity)
    vp = sub.add_parser("vision-preflight", help="offline review of what a real screenshot run would send/cost (no network)")
    vp.add_argument("--model", help="model to price (default: GHOST_AUDIT_VISION_MODEL or gemini-2.5-flash)")
    vp.set_defaults(fn=cmd_vision_preflight)
    vi = sub.add_parser("vision-inspect", help="interpret screenshots with the FAKE client fixture (no network)")
    vi.add_argument("--fake", required=True, help="path to the fake-response fixture JSON")
    vi.set_defaults(fn=cmd_vision_inspect)
    vc = sub.add_parser("vision-compare", help="pipeline with screenshot interpretation disabled vs fake-enabled (no network)")
    vc.add_argument("--fake", required=True)
    vc.set_defaults(fn=cmd_vision_compare)
    vr = sub.add_parser("vision-run", help="REAL Gemini calls: needs GHOST_AUDIT_VISION=gemini, GEMINI_API_KEY and --approve-real-calls")
    vr.add_argument("--approve-real-calls", action="store_true")
    vr.add_argument("--out", default="vision_interpretations.json")
    vr.add_argument("--cache-dir", default=str(REPO_ROOT / "code" / ".cache" / "vision"))
    vr.set_defaults(fn=cmd_vision_run)
    rn = sub.add_parser("run", help="write output.csv for all requests (deterministic, no model calls) and validate it")
    rn.add_argument("--out", help="output path (default: <repo root>/output.csv)")
    rn.add_argument("--screenshots", choices=["transcribed", "none"], default="transcribed",
                    help="use the committed, hash-checked transcription of the 11 PNG screenshots (default) or ignore screenshots")
    rn.add_argument("--case-order", choices=["string", "numeric"], default="string",
                    help="row order: plain string (case_100 first) or numeric (case_33 first); the specification only says 'sorted by case_id'")
    rn.set_defaults(fn=cmd_run)
    co = sub.add_parser("check-output", help="independently validate an output CSV against the dataset (schema, coverage, evidence, cutoff)")
    co.add_argument("file")
    co.add_argument("--case-order", choices=["string", "numeric"], default=None, help="require this row order (default: accept either)")
    co.set_defaults(fn=cmd_check_output)
    pt = sub.add_parser("package-trace", help="assemble agent-trace.zip from the real Claude Code session files plus your /export transcript(s)")
    pt.add_argument("--export", action="append", help="path to a /export transcript (repeatable)")
    pt.add_argument("--out", help="archive path (default: <repo root>/agent-trace.zip)")
    pt.set_defaults(fn=cmd_package_trace)
    vt = sub.add_parser("verify-trace", help="verify agent-trace.zip (structure, JSONL validity, secrets, staleness vs the live session file)")
    vt.add_argument("file", nargs="?")
    vt.set_defaults(fn=cmd_verify_trace)
    pk = sub.add_parser("package", help="build and verify code.zip per the official packaging rules")
    pk.add_argument("--out", help="archive path (default: <repo root>/code.zip)")
    pk.set_defaults(fn=cmd_package)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
