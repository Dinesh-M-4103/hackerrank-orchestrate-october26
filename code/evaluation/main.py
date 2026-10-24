"""Score a predictions CSV against a truth CSV (default: the 32 public samples).

    python code/evaluation/main.py --pred output.csv                     # score predictions on the samples
    python code/evaluation/main.py --baseline all-incomplete             # naive baseline floor
    python code/evaluation/main.py --self-check                          # truth vs truth must score 100%
    python code/evaluation/main.py --custom                              # the participant-written evaluation cases
    python code/evaluation/make_reports.py                               # regenerate evaluation_report.md and usage_report.md

No API key or network access is used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_DIR))

from ghost_audit.loaders import load_requests  # noqa: E402
from ghost_audit.paths import DATASET_DIR  # noqa: E402
from ghost_audit.scoring import BASELINES, read_rows, score  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", help="predictions CSV (case_id,deletion_status,steps_json)")
    ap.add_argument("--truth", default=str(DATASET_DIR / "sample_output.csv"))
    ap.add_argument("--baseline", choices=sorted(BASELINES), help="score a naive baseline instead of --pred")
    ap.add_argument("--self-check", action="store_true", help="score the truth file against itself")
    ap.add_argument("--custom", action="store_true", help="run the participant-written evaluation cases")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ap.add_argument("--per-case", action="store_true", help="include per-case detail in JSON output")
    args = ap.parse_args(argv)

    if args.custom:
        from evaluation.custom_cases import run_custom_cases

        results = run_custom_cases()
        for r in results:
            print(("PASS " if r.passed else "FAIL ") + r.name + ("" if r.passed else "  -> " + "; ".join(r.detail)))
        return 0 if all(r.passed for r in results) else 1
    truth = read_rows(args.truth)
    if args.self_check:
        pred = truth
    elif args.baseline:
        pred = BASELINES[args.baseline](load_requests(sample=True))
    elif args.pred:
        pred = read_rows(args.pred)
    else:
        ap.error("give --pred, --baseline or --self-check")

    report = score(pred, truth)
    print(json.dumps(report.to_dict(per_case=args.per_case), indent=2) if args.json else report.to_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
