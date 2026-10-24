import copy
import csv
import json
import unittest

from ghost_audit.attribution import CONFIRMED, PROBABLE, AccountAttributor
from ghost_audit.context import build_context
from ghost_audit.coverage import coverage_for
from ghost_audit.intent_hints import nondeletion_hints
from ghost_audit.loaders import load_requests
from ghost_audit.models import EvidenceMeta
from ghost_audit.paths import DATASET_DIR
from ghost_audit.schema import OUTPUT_COLUMNS, OPERATIONS, sort_key, validate_csv, validate_steps
from ghost_audit.scoring import BASELINES, CaseRow, lcs_len, read_rows, score

from .support import TempDirCase, needs_dataset


def step(n, phase, op, state, system="account_service", resource="account_1", ev=""):
    return {"step_no": n, "phase": phase, "system_id": system, "resource_id": resource, "operation": op, "state": state, "evidence_id": ev}


GOOD_INCOMPLETE = [step(1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", ev="evidence_1"),
                   step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT"),
                   step(3, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT")]


class SchemaTests(unittest.TestCase):
    def test_valid_plans(self):
        self.assertEqual(validate_steps("c", "INCOMPLETE", GOOD_INCOMPLETE), [])
        self.assertEqual(validate_steps("c", "COMPLETE", [step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", ev="evidence_1")]), [])
        self.assertEqual(validate_steps("c", "NOT_APPLICABLE", []), [])

    def test_no_uncertainty_field_is_allowed(self):
        s = copy.deepcopy(GOOD_INCOMPLETE)
        s[0]["confidence"] = 0.4
        self.assertTrue(validate_steps("c", "INCOMPLETE", s))

    def test_rejections(self):
        def bad(status, steps):
            return [str(p) for p in validate_steps("c", status, steps)]
        s = copy.deepcopy(GOOD_INCOMPLETE)
        s[1]["state"] = "REDACTED"
        self.assertTrue(any("state" in m for m in bad("INCOMPLETE", s)))
        s = copy.deepcopy(GOOD_INCOMPLETE); s[2]["step_no"] = 7
        self.assertTrue(any("step_no" in m for m in bad("INCOMPLETE", s)))
        s = copy.deepcopy(GOOD_INCOMPLETE); s[1]["evidence_id"] = "evidence_9"
        self.assertTrue(any("empty evidence_id" in m for m in bad("INCOMPLETE", s)))
        s = copy.deepcopy(GOOD_INCOMPLETE); s[0]["evidence_id"] = ""
        self.assertTrue(any("cite evidence" in m for m in bad("INCOMPLETE", s)))
        self.assertTrue(bad("NOT_APPLICABLE", GOOD_INCOMPLETE))
        self.assertTrue(bad("MAYBE", []))
        self.assertTrue(bad("COMPLETE", [step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT")]))     # needs evidence
        self.assertTrue(bad("INCOMPLETE", [GOOD_INCOMPLETE[2], GOOD_INCOMPLETE[0]]))                      # phase order

    def test_operation_table_has_the_fifteen_allowed_operations(self):
        self.assertEqual(len(OPERATIONS), 15)
        self.assertEqual(OUTPUT_COLUMNS, ["case_id", "deletion_status", "steps_json"])


class ScoringTests(unittest.TestCase):
    def rows(self, steps, status="INCOMPLETE"):
        return {"c1": CaseRow("c1", status, steps)}

    def test_perfect_prediction(self):
        r = score(self.rows(GOOD_INCOMPLETE), self.rows(GOOD_INCOMPLETE))
        self.assertEqual((r.status_accuracy, r.step_f1, r.sequence_exact_rate, r.case_exact_rate), (1.0, 1.0, 1.0, 1.0))

    def test_missing_step_and_wrong_status(self):
        pred = self.rows(GOOD_INCOMPLETE[:2], "COMPLETE")
        r = score(pred, self.rows(GOOD_INCOMPLETE))
        self.assertEqual(r.status_accuracy, 0.0)
        self.assertAlmostEqual(r.step_recall, 2 / 3)
        self.assertEqual(r.step_precision, 1.0)
        self.assertEqual(r.sequence_exact_rate, 0.0)
        self.assertAlmostEqual(r.sequence_lcs_ratio, 2 * 2 / 5)
        self.assertEqual(r.field_accuracy["phase"], 2 / 3)

    def test_wrong_resource_is_a_false_positive_and_false_negative(self):
        wrong = copy.deepcopy(GOOD_INCOMPLETE)
        wrong[2]["resource_id"] = "account_2"
        r = score(self.rows(wrong), self.rows(GOOD_INCOMPLETE))
        self.assertAlmostEqual(r.step_precision, 2 / 3)
        self.assertAlmostEqual(r.step_recall, 2 / 3)
        self.assertEqual(r.field_accuracy["resource_id"], 2 / 3)

    def test_wrong_evidence_only_affects_evidence_agreement(self):
        wrong = copy.deepcopy(GOOD_INCOMPLETE)
        wrong[0]["evidence_id"] = "evidence_2"
        r = score(self.rows(wrong), self.rows(GOOD_INCOMPLETE))
        self.assertAlmostEqual(r.evidence_accuracy, 2 / 3)

    def test_order_matters_for_sequence_but_not_step_set(self):
        swapped = [GOOD_INCOMPLETE[0], GOOD_INCOMPLETE[2], GOOD_INCOMPLETE[1]]
        r = score(self.rows(swapped), self.rows(GOOD_INCOMPLETE))
        self.assertEqual(r.step_f1, 1.0)
        self.assertEqual(r.sequence_exact_rate, 0.0)

    def test_missing_prediction_counts(self):
        r = score({}, self.rows(GOOD_INCOMPLETE))
        self.assertEqual((r.n_missing, r.status_accuracy), (1, 0.0))

    def test_lcs(self):
        self.assertEqual(lcs_len("abcde", "ace"), 3)
        self.assertEqual(lcs_len([], [1]), 0)


@needs_dataset
class SampleAnswerTests(unittest.TestCase):
    """The official samples validate the schema validator, scorer and attribution (they are never used to choose answers)."""

    @classmethod
    def setUpClass(cls):
        cls.truth = read_rows(DATASET_DIR / "sample_output.csv")

    def test_validator_accepts_all_official_samples(self):
        self.assertEqual(validate_csv(DATASET_DIR / "sample_output.csv", [c.case_id for c in load_requests(sample=True)]), [])

    def test_samples_follow_the_documented_ordering_rule(self):
        for cid, row in self.truth.items():
            self.assertEqual(row.steps, sorted(row.steps, key=sort_key), cid)

    def test_scorer_self_check_and_baselines(self):
        self.assertEqual(score(self.truth, self.truth).case_exact_rate, 1.0)
        sample_cases = load_requests(sample=True)
        floor = score(BASELINES["all-incomplete"](sample_cases), self.truth)
        self.assertAlmostEqual(floor.status_accuracy, 22 / 32)
        claim = score(BASELINES["reported-status"](sample_cases), self.truth)
        self.assertAlmostEqual(claim.status_accuracy, 17 / 32)

    def test_blank_template_header_matches_schema(self):
        with open(DATASET_DIR / "output.csv", encoding="utf-8-sig") as fh:
            self.assertEqual(next(csv.reader(fh)), OUTPUT_COLUMNS)


@needs_dataset
class RealAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = build_context()
        ev, sm = load_requests(), load_requests(sample=True)
        hints = nondeletion_hints(ev, cls.ctx.activities, cls.ctx.owners, cls.ctx.resolver, strict_cutoff=False)
        cls.sample_ids = {c.case_id for c in sm}
        cls.results = AccountAttributor(cls.ctx.resolver, cls.ctx.owners, cls.ctx.activities).attribute(
            [c for c in ev + sm if c.case_id not in hints])
        truth = read_rows(DATASET_DIR / "sample_output.csv")
        cls.expected = {cid: next((s["resource_id"] for s in r.steps if s["system_id"] == "account_service"), None) for cid, r in truth.items()}

    def test_every_asserted_sample_account_is_correct(self):
        asserted = {cid: r for cid, r in self.results.items() if cid in self.sample_ids and r.accounts}
        self.assertGreaterEqual(len(asserted), 15)
        for cid, r in asserted.items():
            self.assertEqual(list(r.accounts), [self.expected[cid]], f"{cid} {r.status}/{r.basis}")

    def test_ambiguous_sample_cases_keep_the_true_account_in_their_candidates_and_assert_nothing(self):
        amb = [r for cid, r in self.results.items() if cid in self.sample_ids and r.status == "AMBIGUOUS"]
        self.assertTrue(amb)
        for r in amb:
            self.assertEqual(r.accounts, ())
            self.assertIn(self.expected[r.case_id], r.candidates)

    def test_nondeletion_hint_flags_exactly_the_ten_classified_requests(self):
        hints = nondeletion_hints(load_requests(), self.ctx.activities, self.ctx.owners, self.ctx.resolver, strict_cutoff=False)
        self.assertEqual(len(hints), 10)

    def test_post_audit_evidence_is_never_used_under_the_strict_cutoff(self):
        by_case = {c.case_id: c for c in load_requests() + load_requests(sample=True)}
        for cid, r in self.results.items():
            if r.status == CONFIRMED:
                for fid in r.evidence_ids:
                    cap = next((m.captured_at for m in self.ctx.manifest if m.file_id == fid), None)
                    if cap:
                        self.assertLessEqual(cap, by_case[cid].audit_at, cid)


class CoverageTests(TempDirCase):
    def test_failures_and_unsupported_are_reported_separately(self):
        good = self.meta("logs/good.log", '2026-09-01T00:00:00Z level=INFO service=x trace_id=t event=e resource_id=account_1 rows=1\n', "evidence_g")
        bad = self.meta("logs/bad.log", "garbage line\n", "evidence_b")
        img = self.meta("images/x.png", b"\x89PNG", "evidence_i")
        gone = EvidenceMeta("evidence_m", "evidence/logs/missing.log", self.tmp / "missing.log", "t", "2026-09-01T00:00:00Z")
        rep = coverage_for([good, bad, img, gone])
        self.assertEqual({f.file_id for f in rep.failures}, {"evidence_b", "evidence_m"})
        self.assertEqual([f.file_id for f in rep.unsupported], ["evidence_i"])
        self.assertFalse(rep.passed)
        self.assertIn("need vision model", rep.summary())

    @needs_dataset
    def test_real_dataset_has_no_parse_failures(self):
        ctx = build_context()
        rep = coverage_for(ctx.manifest, ctx.extractions)
        self.assertTrue(rep.passed, rep.summary())
        self.assertEqual(len(rep.files), 94)
        self.assertEqual(len(rep.unsupported), 11)


if __name__ == "__main__":
    unittest.main()
