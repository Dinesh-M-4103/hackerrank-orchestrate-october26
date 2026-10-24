import json
import unittest

from ghost_audit.intent import DELETION, NON_DELETION, UNCERTAIN, classify_intent, decide
from ghost_audit.pipeline import OWNERSHIP_LINK_ONLY, rows_for
from ghost_audit.report import ambiguity_effect, mismatches
from ghost_audit.schema import OPERATIONS, sort_key, validate_steps
from ghost_audit.scoring import CaseRow

from .support import F, S, deletion_case, make_pipeline, submit

BOB = ["bob@x.com"]


def plan_of(pipeline, case):
    return pipeline.run([case])[case.case_id]


def ops(plan, phase=None):
    return [(s["operation"], s["resource_id"], s["evidence_id"]) for s in plan.steps if phase is None or s["phase"] == phase]


class OrderingAndShapeTests(unittest.TestCase):
    def full_facts(self):
        return [
            F("e1", "account_1", emails=BOB, category="USER_PROFILE", system="account_service"),
            F("e1", "search_1", emails=BOB),
            F("e1", "support_1", emails=BOB, message_text="send to bob@x.com"),
            F("e1", "analytics_1", user_id="user_2", email="bob@x.com", emails=BOB),
            F("e1", "tombstone_1", refs=["user_2"], subject_reference="user_2", name="Bob"),
            F("e1", "data_flow_1", emails=BOB, enabled=True),
            F("e1", "backup_1", emails=BOB, restore_allowed=True),
            F("e1", "audit_record_1", refs=["user_2"], retention_until="2031-12-31T23:59:59Z"),
            F("e1", "export_1", emails=BOB),
        ]

    def test_exact_operation_and_resource_ordering(self):
        p = make_pipeline(self.full_facts(), [submit("a1", "account_1", "2026-09-01T11:00:00Z")])
        plan = plan_of(p, deletion_case())
        self.assertEqual(plan.status, "INCOMPLETE")
        self.assertEqual(ops(plan, "FINDING"), [
            ("REPORT_REMAINING_RECORD", "account_1", "e1"), ("REPORT_REMAINING_RECORD", "export_1", "e1"), ("REPORT_REMAINING_RECORD", "search_1", "e1"),
            # analytics_platform < identity_service < support_platform
            ("REPORT_EXPOSED_PII", "analytics_1", "e1"), ("REPORT_EXPOSED_PII", "tombstone_1", "e1"), ("REPORT_EXPOSED_PII", "support_1", "e1"),
            ("REPORT_ACTIVE_DATA_FLOW", "data_flow_1", "e1"), ("REPORT_RESTORE_RISK", "backup_1", "e1")])
        self.assertEqual([o for o, _, _ in ops(plan, "REMEDIATION")], [
            "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "DELETE_RECORD", "DELETE_RECORD", "REDACT_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"])
        self.assertEqual([r for _, r, _ in ops(plan, "REMEDIATION")][2:5], ["account_1", "export_1", "search_1"])      # system_id, resource_id order
        self.assertEqual([o for o, _, _ in ops(plan, "VERIFICATION")], [
            "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
            "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"])
        self.assertEqual([s["step_no"] for s in plan.steps], list(range(1, len(plan.steps) + 1)))
        self.assertEqual(plan.steps, sorted(plan.steps, key=sort_key))
        self.assertEqual(validate_steps("c1", plan.status, plan.steps), [])
        phases = [s["phase"] for s in plan.steps]
        self.assertEqual(phases, sorted(phases, key=["FINDING", "REMEDIATION", "VERIFICATION"].index))

    def test_remediation_and_future_verification_have_empty_evidence_and_findings_cite_real_files(self):
        plan = plan_of(make_pipeline(self.full_facts(), [submit("a1", "account_1", "2026-09-01T11:00:00Z")]), deletion_case())
        for s in plan.steps:
            if s["phase"] == "FINDING":
                self.assertEqual(s["evidence_id"], "e1")
            else:
                self.assertEqual(s["evidence_id"], "")

    def test_serialization_is_deterministic_and_uses_exactly_the_seven_fields(self):
        p = make_pipeline(self.full_facts(), [submit("a1", "account_1", "2026-09-01T11:00:00Z")])
        row1, row2 = plan_of(p, deletion_case()).to_row(), plan_of(p, deletion_case()).to_row()
        self.assertEqual(row1, row2)
        self.assertEqual(list(json.loads(row1["steps_json"])[0]), ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"])

    def test_operation_table_order_is_the_spec_order(self):
        names = [op for _, op, _ in OPERATIONS]
        self.assertEqual(names[:4], ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"])
        self.assertEqual(names[4:9], ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"])


class StatusTests(unittest.TestCase):
    def compliant_facts(self):
        return [F("e1", "account_1", state="NOT_FOUND", emails=BOB, category="USER_PROFILE", system="account_service"),
                F("e1", "audit_record_1", refs=["user_2"], retention_until="2031-12-31T23:59:59Z"),
                F("e1", "analytics_1", user_id=None, email=None, emails=BOB),
                F("e1", "support_1", message_text="[REDACTED]", emails=BOB)]

    def test_complete_case_has_only_cited_verification_steps(self):
        plan = plan_of(make_pipeline(self.compliant_facts(), [submit("a1", "account_1", "2026-09-01T11:00:00Z")]), deletion_case())
        self.assertEqual(plan.status, "COMPLETE")
        self.assertTrue(plan.steps)
        self.assertTrue(all(s["phase"] == "VERIFICATION" and s["evidence_id"] == "e1" for s in plan.steps))
        self.assertEqual(validate_steps("c1", plan.status, plan.steps), [])

    def test_one_violation_makes_the_case_incomplete_regardless_of_workflow_claims(self):
        facts = self.compliant_facts() + [F("e1", "search_1", emails=BOB)]
        for reported in ("COMPLETE", "PARTIALLY_COMPLETE", "NOT_STARTED"):
            plan = plan_of(make_pipeline(facts, [submit("a1", "account_1", "2026-09-01T11:00:00Z")]), deletion_case(status=reported))
            self.assertEqual(plan.status, "INCOMPLETE", reported)

    def test_reported_status_never_changes_the_plan(self):
        p = make_pipeline(self.compliant_facts(), [submit("a1", "account_1", "2026-09-01T11:00:00Z")])
        rows = {s: plan_of(p, deletion_case(status=s)).to_row() for s in ("COMPLETE", "PARTIALLY_COMPLETE", "NOT_STARTED")}
        self.assertEqual(len({json.dumps(r, sort_keys=True) for r in rows.values()}), 1)
        self.assertEqual(rows["NOT_STARTED"]["deletion_status"], "COMPLETE")

    def test_workflow_success_claims_do_not_hide_a_surviving_record(self):
        from ghost_audit.models import Activity
        claim = Activity("activity_9", "2026-09-01T00:00:00Z", "DELETE_RECORD", "deletion_worker", "search_service", "search_1", "SUCCESS", "done")
        plan = plan_of(make_pipeline([F("e1", "search_1", emails=BOB)], [claim]), deletion_case(status="COMPLETE"))
        self.assertEqual(plan.status, "INCOMPLETE")
        self.assertIn(("REPORT_REMAINING_RECORD", "search_1", "e1"), ops(plan, "FINDING"))

    def test_nothing_assessable_is_incomplete_and_flagged_never_a_guessed_complete(self):
        plan = plan_of(make_pipeline([], []), deletion_case(status="COMPLETE"))
        self.assertEqual((plan.status, plan.steps), ("INCOMPLETE", []))
        self.assertIn("insufficient_evidence", plan.flags)

    def test_unknown_email_cannot_be_planned(self):
        plan = plan_of(make_pipeline(self.compliant_facts()), deletion_case(email="nobody@x.com"))
        self.assertEqual(plan.steps, [])
        self.assertIn("insufficient_evidence", plan.flags)

    def test_historical_email_reaches_the_same_person_and_resources(self):
        facts = [F("e1", "search_1", emails=["ana.old@x.com"])]
        plan = plan_of(make_pipeline(facts), deletion_case(email="ana.new@x.com"))
        self.assertIn(("REPORT_REMAINING_RECORD", "search_1", "e1"), ops(plan, "FINDING"))
        plan2 = plan_of(make_pipeline(facts), deletion_case(email="ana.old@x.com"))
        self.assertEqual(plan.steps, plan2.steps)

    def test_evidence_after_audit_cannot_change_the_plan(self):
        base = self.compliant_facts()
        later = base + [F("late", "search_1", emails=BOB, captured="2026-09-10T00:00:00Z")]
        a = plan_of(make_pipeline(base, [submit("a1", "account_1", "2026-09-01T11:00:00Z")]), deletion_case())
        b = plan_of(make_pipeline(later, [submit("a1", "account_1", "2026-09-01T11:00:00Z")]), deletion_case())
        self.assertEqual((a.status, a.steps), (b.status, b.steps))
        self.assertEqual(a.status, "COMPLETE")

    def test_recreated_record_after_deletion_is_a_finding(self):
        facts = [F("e0", "search_1", state="NOT_FOUND", emails=BOB, captured="2026-09-01T00:00:00Z"),
                 F("e1", "search_1", emails=BOB, captured="2026-09-02T00:00:00Z")]
        plan = plan_of(make_pipeline(facts), deletion_case())
        self.assertEqual(plan.status, "INCOMPLETE")
        self.assertIn("recreated", [f for it in plan.items for f in it.state.flags])

    def test_replica_survivor_is_found_even_though_the_primary_is_clean(self):
        facts = [F("db", "account_1", role="failover_replica", emails=BOB, category="USER_PROFILE", system="account_service")]
        scopes = [S("db", "account_service", "customer_profiles_primary", role="primary"),
                  S("db", "account_service", "customer_profiles_failover_replica", role="failover_replica")]
        plan = plan_of(make_pipeline(facts, [submit("a1", "account_1", "2026-09-01T11:00:00Z")], scopes), deletion_case())
        self.assertEqual(plan.status, "INCOMPLETE")
        self.assertEqual(ops(plan, "FINDING"), [("REPORT_REMAINING_RECORD", "account_1", "db")])
        self.assertIn("replica_drift:account_1", plan.flags)

    def test_audit_record_and_legal_hold_are_verified_present_never_deleted(self):
        facts = [F("e1", "audit_record_1", refs=["user_2"], retention_until="2031-12-31T23:59:59Z"),
                 F("e1", "legal_hold_1", refs=["user_2"], retained_until="2033-12-31T23:59:59Z")]
        plan = plan_of(make_pipeline(facts), deletion_case())
        self.assertEqual(plan.status, "COMPLETE")
        self.assertEqual([o for o, _, _ in ops(plan)], ["VERIFY_RECORD_PRESENT", "VERIFY_RECORD_PRESENT"])
        self.assertNotIn("DELETE_RECORD", [o for o, _, _ in ops(plan)])

    def test_expired_retention_gets_no_invented_step(self):
        plan = plan_of(make_pipeline([F("e1", "audit_record_1", refs=["user_2"], retention_until="2026-01-01T00:00:00Z")]), deletion_case())
        self.assertEqual(plan.steps, [])

    def test_blocked_backup_is_verified_and_a_restorable_one_is_blocked_not_deleted(self):
        ok = plan_of(make_pipeline([F("e1", "backup_1", emails=BOB, restore_allowed=False)]), deletion_case())
        self.assertEqual((ok.status, ops(ok)), ("COMPLETE", [("VERIFY_RESTORE_BLOCKED", "backup_1", "e1")]))
        bad = plan_of(make_pipeline([F("e1", "backup_1", emails=BOB, restore_allowed=True)]), deletion_case())
        self.assertEqual(bad.status, "INCOMPLETE")
        self.assertEqual([o for o, _, _ in ops(bad)], ["REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"])

    def test_shared_attachment_is_not_deleted(self):
        att = F("e1", "support_5", emails=BOB, refs=["user_3"], category="SUPPORT_ATTACHMENT", file_name="x.pdf")
        plan = plan_of(make_pipeline([att]), deletion_case())
        self.assertEqual(plan.steps, [])
        solo = F("e1", "support_5", emails=BOB, category="SUPPORT_ATTACHMENT", file_name="x.pdf")
        self.assertIn(("DELETE_RECORD", "support_5", ""), ops(plan_of(make_pipeline([solo]), deletion_case()), "REMEDIATION"))

    def test_stale_links_from_an_earlier_deletion_run_are_not_pulled_in(self):
        old = F("old", "search_9", emails=BOB, captured="2026-03-01T00:00:00Z")
        self.assertEqual(plan_of(make_pipeline([old]), deletion_case()).steps, [])


class AmbiguityTests(unittest.TestCase):
    def two_cases(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z"), submit("a2", "account_8", "2026-09-01T11:10:00Z")]
        return acts, [deletion_case("c1", "bob@x.com"), deletion_case("c2", "cy@x.com")]

    def test_ambiguous_account_is_never_asserted_but_confirmed_resources_still_planned(self):
        acts, cases = self.two_cases()
        facts = [F("e1", "search_1", emails=BOB), F("e1", "account_7", state="NOT_FOUND", category="USER_PROFILE", system="account_service")]
        plans = make_pipeline(facts, acts, [S("snap", "account_service", "customer_profiles", role="inventory")]).run(cases)
        p = plans["c1"]
        self.assertEqual(p.attribution.status, "AMBIGUOUS")
        self.assertFalse([s for s in p.steps if s["system_id"] == "account_service"])
        self.assertIn(("REPORT_REMAINING_RECORD", "search_1", "e1"), ops(p, "FINDING"))
        self.assertEqual(p.status, "INCOMPLETE")
        self.assertIn("ambiguous_account", p.flags)

    def test_candidates_in_the_same_state_cannot_change_the_status(self):
        acts, cases = self.two_cases()
        scopes = [S("snap", "account_service", "customer_profiles", role="inventory")]
        plans = make_pipeline([F("e1", "audit_record_1", refs=["user_2"], retention_until="2031-01-01T00:00:00Z")], acts, scopes).run(cases)
        p = plans["c1"]
        self.assertEqual(p.candidate_states, {"account_7": "ABSENT", "account_8": "ABSENT"})
        self.assertNotIn("status_at_risk", p.flags)
        self.assertEqual(ambiguity_effect(plans)["all_candidates_same_state"], 2)

    def test_a_present_candidate_would_put_the_status_at_risk(self):
        acts, cases = self.two_cases()
        facts = [F("e1", "audit_record_1", refs=["user_2"], retention_until="2031-01-01T00:00:00Z"),
                 F("e1", "account_8", category="USER_PROFILE", system="account_service")]      # present but with no identity (not owned)
        p = make_pipeline(facts, acts).run(cases)["c1"]
        self.assertIn("status_at_risk", p.flags)

    def test_probable_account_is_asserted_with_its_basis(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z")]
        scopes = [S("snap", "account_service", "customer_profiles", role="inventory")]
        plan = plan_of(make_pipeline([], acts, scopes), deletion_case())
        self.assertEqual(plan.attribution.status, "PROBABLE")
        self.assertEqual(plan.status, "COMPLETE")
        self.assertEqual(ops(plan), [("VERIFY_RECORD_ABSENT", "account_7", "snap")])

    def test_probable_account_without_state_evidence_is_only_a_planned_check_in_incomplete_cases(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z")]
        inc = plan_of(make_pipeline([F("e1", "search_1", emails=BOB)], acts), deletion_case())
        self.assertIn(("VERIFY_RECORD_ABSENT", "account_7", ""), ops(inc, "VERIFICATION"))
        comp = plan_of(make_pipeline([F("e1", "audit_record_1", refs=["user_2"], retention_until="2031-01-01T00:00:00Z")], acts), deletion_case())
        self.assertEqual(comp.status, "COMPLETE")
        self.assertNotIn("account_7", [r for _, r, _ in ops(comp)])      # a COMPLETE case may only assert what it can cite

    def test_owned_resource_with_unobserved_state_gets_a_planned_check_only_when_incomplete(self):
        facts = [F("e1", "search_1", emails=BOB), F("e1", "audit_record_1", refs=["user_2"], captured="2026-09-04T00:00:00Z",
                                                    retention_until="2031-01-01T00:00:00Z")]
        plan = plan_of(make_pipeline(facts, ownership=OWNERSHIP_LINK_ONLY), deletion_case())
        self.assertIn(("VERIFY_RECORD_PRESENT", "audit_record_1", ""), ops(plan, "VERIFICATION"))
        strict = plan_of(make_pipeline(facts), deletion_case())
        self.assertNotIn("audit_record_1", [r for _, r, _ in ops(strict)])      # strict: the link itself is post-audit evidence


class NotApplicableTests(unittest.TestCase):
    def test_non_deletion_request_is_not_applicable_with_empty_steps_even_when_resources_exist(self):
        facts = [F("e1", "search_1", emails=BOB), F("e1", "account_1", emails=BOB, category="USER_PROFILE", system="account_service")]
        plan = plan_of(make_pipeline(facts, [submit("a1", "account_1", "2026-09-01T11:00:00Z")]),
                       deletion_case(text="Please stop the promotional emails. I still need the account, so I am not asking for deletion.", status="COMPLETE"))
        self.assertEqual((plan.status, plan.steps), ("NOT_APPLICABLE", []))
        self.assertEqual(plan.to_row(), {"case_id": "c1", "deletion_status": "NOT_APPLICABLE", "steps_json": "[]"})

    def test_non_deletion_requests_do_not_consume_account_candidates_of_deletion_cases(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z")]
        cases = [deletion_case("c1", "bob@x.com"), deletion_case("c2", "cy@x.com", text="Could you send me a copy of my data? Do not delete anything.")]
        plans = make_pipeline([], acts, [S("snap", "account_service", "customer_profiles", role="inventory")]).run(cases)
        self.assertEqual(plans["c2"].status, "NOT_APPLICABLE")
        self.assertEqual(plans["c1"].attribution.status, "PROBABLE")       # would be a conflict if c2 had been treated as a deletion


class IntentTests(unittest.TestCase):
    def test_deletion_in_many_languages(self):
        texts = [
            "Please permanently delete the account associated with this email address.",
            "Solicito la eliminación permanente de la cuenta asociada a este correo.",
            "Solicito a exclusão permanente da conta associada a este e-mail.",
            "Je demande la suppression définitive du compte associé à cette adresse e-mail.",
            "Bitte löschen Sie mein Konto und alle personenbezogenen Daten.",
            "このメールアドレスに関連するアカウントの完全な削除を依頼します。",
            "이 이메일 주소에 연결된 계정의 영구 삭제를 요청합니다.",
            "Vorrei che il mio account fosse eliminato definitivamente.",
            "I deleted my account and received the confirmation email. Could you verify that the profile is gone?",
            "Please close my account and process the associated personal-data deletion.",
            "I changed the email on this account before deleting it. Please check that nothing tied to the old address was left behind.",
        ]
        for t in texts:
            self.assertEqual(classify_intent(t).label, DELETION, t)

    def test_non_deletion_requests(self):
        texts = [
            "I would like a copy of all the data you hold about me.",
            "Please correct the spelling of my surname on the account.",
            "I can't log in; please reset my password.",
            "Unsubscribe me from the newsletter, please.",
            "Please remove me from marketing lists only. I still want receipts.",
            "Can you export my order history? Please do not delete anything.",
            "Please change my phone number; it is out of date.",
            "This is an information request, not a request to erase my data.",
            "Please do not delete my account.",
            "I want to keep my account but stop the promotional emails.",
        ]
        for t in texts:
            self.assertEqual(classify_intent(t).label, NON_DELETION, t)

    def test_allowances_inside_a_deletion_request_are_not_refusals(self):
        texts = [
            "I'm deleting my account. You can keep anonymous statistics, but nothing in analytics should still point back to me.",
            "I want to close this account permanently. You may retain backups if required, but they must not be able to restore my profile.",
            "Please delete this account. Security records can stay if policy requires them.",
            "I no longer need this account. Please remove my name and contact details, but keeping an internal deleted-account reference is fine.",
            "Please delete my account. Do not delete my invoices.",
            "Can you verify that my account was deleted? Please do not treat that similar record as mine.",
            "I still want my account deleted.",
        ]
        for t in texts:
            self.assertEqual(classify_intent(t).label, DELETION, t)

    def test_hedged_and_conditional_wording(self):
        self.assertEqual(classify_intent("I might decide to delete my account next month.").label, NON_DELETION)
        self.assertEqual(classify_intent("Please close my account. Don't close it before refunding me.").label, DELETION)

    def test_no_cue_is_uncertain_not_silently_either(self):
        self.assertEqual(classify_intent("Hello, thanks!").label, UNCERTAIN)
        self.assertFalse(classify_intent("").authorizes_deletion)

    def test_classify_activity_is_corroboration_not_a_substitute(self):
        deletion = decide("Please delete my account.", ["activity_9"])
        self.assertEqual(deletion.label, DELETION)                              # text wins
        self.assertTrue(any("conflict" in r for r in deletion.reasons))
        nd = decide("Please correct my surname.", ["activity_9"])
        self.assertEqual(nd.label, NON_DELETION)
        self.assertTrue(any("corroborated" in r for r in nd.reasons))
        tie = decide("Hello, thanks!", ["activity_9"])                          # only an undecidable text may lean on the hint
        self.assertEqual(tie.label, NON_DELETION)
        self.assertEqual(decide("Hello, thanks!", None).label, UNCERTAIN)


class ReportTests(unittest.TestCase):
    def test_mismatch_report_names_missing_extra_and_status_differences(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z")]
        plans = make_pipeline([F("e1", "search_1", emails=BOB)], acts).run([deletion_case()])
        pred = rows_for(plans)
        truth = {"c1": CaseRow("c1", "COMPLETE", [{"step_no": 1, "phase": "VERIFICATION", "system_id": "security_service",
                                                  "resource_id": "audit_record_1", "operation": "VERIFY_RECORD_PRESENT", "state": "PRESENT", "evidence_id": "x"}])}
        mm = mismatches(plans, pred, truth)
        self.assertEqual(len(mm), 1)
        self.assertEqual(mm[0]["status"], ("COMPLETE", "INCOMPLETE"))
        self.assertTrue(mm[0]["missing"] and mm[0]["extra"])


if __name__ == "__main__":
    unittest.main()
