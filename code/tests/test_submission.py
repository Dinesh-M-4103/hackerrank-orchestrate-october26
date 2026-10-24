import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ghost_audit.packaging import build_code_zip, collect, verify_zip
from ghost_audit.schema import validate_steps
from ghost_audit.submission import check_output, run_pipeline, write_output

from .support import needs_dataset


def step(n, phase, op, state, system="search_service", resource="search_1", ev=""):
    return {"step_no": n, "phase": phase, "system_id": system, "resource_id": resource, "operation": op, "state": state, "evidence_id": ev}


class OrderingRuleTests(unittest.TestCase):
    def test_steps_must_follow_operation_then_system_then_resource_order(self):
        a = step(1, "REMEDIATION", "DELETE_RECORD", "ABSENT", "account_service", "account_1")
        b = step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT", "search_service", "search_1")
        self.assertEqual(validate_steps("c", "INCOMPLETE", [step(0, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", ev="e") | {"step_no": 1},
                                                            dict(a, step_no=2), dict(b, step_no=3)]), [])
        swapped = [dict(b, step_no=1), dict(a, step_no=2)]
        self.assertTrue(any("not ordered" in str(p) for p in validate_steps("c", "INCOMPLETE", swapped)))
        wrong_op = [step(1, "REMEDIATION", "REDACT_RECORD", "REDACTED"), step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT")]
        self.assertTrue(any("not ordered" in str(p) for p in validate_steps("c", "INCOMPLETE", wrong_op)))

    def test_repeated_step_is_rejected(self):
        dup = [step(1, "REMEDIATION", "DELETE_RECORD", "ABSENT"), step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT")]
        self.assertTrue(any("more than once" in str(p) for p in validate_steps("c", "INCOMPLETE", dup)))


class PackagingTests(unittest.TestCase):
    def make_repo(self, root: Path):
        files = {
            "README.md": "# readme\n", "requirements.txt": "pypdf\n", "requirements-gemini.txt": "google-genai\n",
            "code/main.py": "print(1)\n", "code/evaluation/main.py": "print(2)\n",
            "code/evaluation/evaluation_report.md": "# r\n", "code/evaluation/usage_report.md": "# u\n",
            "code/ghost_audit/__init__.py": "",
            # things that must never be packaged:
            ".env": "GEMINI_API_KEY=\"LIVE-SECRET-VALUE-0123456789\"\n", "code/.env": "GEMINI_API_KEY=x\n", "code/.env.local": "x\n",
            "code/__pycache__/m.cpython-314.pyc": "x", "code/ghost_audit/__pycache__/a.pyc": "x", "code/.cache/vision/a.json": "{}",
            "dataset/user_identities.csv": "id\n", ".venv/Lib/x.py": "x", "code.zip": "old", "code/nested.zip": "x", "code/models.tar.gz": "x",
            "code/secrets.pem": "x", "vision_interpretations.json": "{}", "code/token.key": "x", "output.csv": "x", "log.txt": "x",
        }
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    def test_collect_includes_only_the_official_content(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.make_repo(root)
            names = {str(p) for p in collect(root)}
            self.assertEqual(names, {"README.md", "requirements.txt", "requirements-gemini.txt", "code/main.py", "code/evaluation/main.py",
                                     "code/evaluation/evaluation_report.md", "code/evaluation/usage_report.md", "code/ghost_audit/__init__.py"})

    def test_built_zip_verifies_and_is_reproducible(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.make_repo(root)
            a, b = root / "a.zip", root / "b.zip"
            build_code_zip(root, a)
            build_code_zip(root, b)
            self.assertEqual(verify_zip(a, "LIVE-SECRET-VALUE-0123456789"), [])
            self.assertEqual(a.read_bytes(), b.read_bytes())
            with zipfile.ZipFile(a) as zf:
                self.assertTrue(all("\\" not in n for n in zf.namelist()))

    def test_verifier_catches_every_forbidden_thing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bad = root / "bad.zip"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("README.md", "ok")
                zf.writestr("code/main.py", "KEY = 'AIza" + "A" * 35 + "'\n")                       # secret-looking text
                zf.writestr("code/evaluation/main.py", "x")
                zf.writestr("code/.env", "GEMINI_API_KEY=x")                                      # .env
                zf.writestr("dataset/x.csv", "x")                                                  # dataset
                zf.writestr("code/__pycache__/a.pyc", "x")                                         # cache
                zf.writestr("code/inner.zip", "x")                                                 # nested archive
                zf.writestr("stray.txt", "x")                                                      # unexpected top-level entry
                zf.writestr("code/live.txt", "has LIVE-SECRET-VALUE-0123456789 inside")            # a live secret value
            kinds = {p.kind for p in verify_zip(bad, "LIVE-SECRET-VALUE-0123456789")}
            self.assertGreaterEqual(kinds, {"missing required file", "forbidden file", "forbidden directory", "nested archive",
                                            "unexpected top-level entry", "contains a live secret", "secret-looking text"})


@needs_dataset
class RealOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        cls.plans = run_pipeline()
        cls.path = cls.dir / "output.csv"
        cls.order = write_output(cls.plans, cls.path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def rows(self):
        with open(self.path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def write_variant(self, rows, header=("case_id", "deletion_status", "steps_json")):
        p = self.dir / "variant.csv"
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            for r in rows:
                w.writerow([r["case_id"], r["deletion_status"], r["steps_json"]])
        return p

    @staticmethod
    def failed(report):
        return {c.name for c in report.checks if not c.ok and c.critical}

    def test_the_real_output_passes_every_check(self):
        rep = check_output(self.path)
        self.assertTrue(rep.passed, rep.to_markdown())
        self.assertEqual(rep.stats["rows"], 518)
        self.assertEqual(rep.stats["statuses"].get("NOT_APPLICABLE"), 10)

    def test_file_format_matches_the_sample(self):
        raw = self.path.read_bytes()
        self.assertTrue(raw.startswith(b"case_id,deletion_status,steps_json\r\n"))
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(raw.endswith(b"\r\n"))
        self.assertEqual(self.order, sorted(self.order))

    def test_run_is_deterministic(self):
        again = run_pipeline()
        self.assertEqual({k: v.to_row() for k, v in again.items()}, {k: v.to_row() for k, v in self.plans.items()})

    def test_checker_detects_tampering(self):
        rows = self.rows()
        by = {r["case_id"]: r for r in rows}
        # a row removed
        self.assertTrue(self.failed(check_output(self.write_variant(rows[1:]))))
        # wrong header
        self.assertTrue(any("header" in n for n in self.failed(check_output(self.write_variant(rows, ("case_id", "status", "steps_json"))))))
        # unsorted
        self.assertTrue(any("sorted" in n for n in self.failed(check_output(self.write_variant(list(reversed(rows)))))))
        # invalid status
        bad = [dict(r) for r in rows]
        bad[0]["deletion_status"] = "MAYBE"
        self.assertTrue(any("allowed" in n for n in self.failed(check_output(self.write_variant(bad)))))
        # NOT_APPLICABLE with steps
        na = next(r for r in rows if r["deletion_status"] == "NOT_APPLICABLE")
        steps = json.dumps([step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", ev="evidence_01")])
        bad = [dict(r, steps_json=steps) if r is na else dict(r) for r in rows]
        self.assertTrue(any("NOT_APPLICABLE" in n for n in self.failed(check_output(self.write_variant(bad)))))
        # evidence that does not exist / is not a real ID
        victim = next(r for r in rows if r["deletion_status"] == "INCOMPLETE" and '"FINDING"' in r["steps_json"])
        steps = json.loads(victim["steps_json"])
        first = next(s for s in steps if s["phase"] == "FINDING")
        first["evidence_id"] = "evidence_9999"
        bad = [dict(r, steps_json=json.dumps(steps)) if r is victim else dict(r) for r in rows]
        self.assertTrue(any("real file_id" in n for n in self.failed(check_output(self.write_variant(bad)))))
        # evidence captured after the case's audit_at (evidence_03 is dated 2026-10-01T12:00:00Z; pick a case audited earlier)
        from ghost_audit.loaders import load_requests
        early = next(c for c in load_requests() if c.audit_at < "2026-10-01T12:00:00Z" and by[c.case_id]["deletion_status"] == "INCOMPLETE"
                     and '"FINDING"' in by[c.case_id]["steps_json"])
        steps = json.loads(by[early.case_id]["steps_json"])
        next(s for s in steps if s["phase"] == "FINDING")["evidence_id"] = "evidence_03"
        bad = [dict(r, steps_json=json.dumps(steps)) if r["case_id"] == early.case_id else dict(r) for r in rows]
        self.assertTrue(any("after the case's audit_at" in n for n in self.failed(check_output(self.write_variant(bad)))))
        # a screenshot is allowed as evidence, but only when it is admissible: evidence_04 is dated 2026-09-26T11:00:00Z
        early_shot = next(c for c in load_requests() if c.audit_at < "2026-09-26T11:00:00Z" and by[c.case_id]["deletion_status"] == "INCOMPLETE"
                          and '"FINDING"' in by[c.case_id]["steps_json"])
        steps = json.loads(by[early_shot.case_id]["steps_json"])
        next(s for s in steps if s["phase"] == "FINDING")["evidence_id"] = "evidence_04"
        bad = [dict(r, steps_json=json.dumps(steps)) if r["case_id"] == early_shot.case_id else dict(r) for r in rows]
        self.assertTrue(any("after the case's audit_at" in n for n in self.failed(check_output(self.write_variant(bad)))))

    def test_every_cited_evidence_is_before_the_audit_cutoff(self):
        rep = check_output(self.path)
        late = [c for c in rep.checks if "after the case's audit_at" in c.name][0]
        self.assertTrue(late.ok, late.detail)

    def test_reported_status_is_not_what_decides_the_output(self):
        from collections import Counter
        from ghost_audit.loaders import load_requests
        by = {c.case_id: c.reported_status for c in load_requests()}
        agree = Counter((by[cid], p.status) for cid, p in self.plans.items() if p.status != "NOT_APPLICABLE")
        # if the claim drove the answer, COMPLETE claims would map only to COMPLETE; they map to both
        self.assertGreater(agree[("COMPLETE", "INCOMPLETE")], 0)
        self.assertGreater(agree[("NOT_STARTED", "COMPLETE")], 0)


class CustomCaseTests(unittest.TestCase):
    def test_all_participant_cases_pass(self):
        from evaluation.custom_cases import run_custom_cases
        results = run_custom_cases()
        self.assertGreaterEqual(len(results), 3)
        self.assertEqual([r.name for r in results if not r.passed], [])


if __name__ == "__main__":
    unittest.main()
