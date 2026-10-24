import unittest

from ghost_audit.models import Activity, Rule
from ghost_audit.observations import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, EXPOSED, PRESENT, REDACTED, RESTORABLE, UNKNOWN,
                                      interpret)
from ghost_audit.policy import Context, PolicyEngine
from ghost_audit.state import StateConfig, StateReconstructor

from .support import F, RULES, S

AUDIT = "2026-09-03T12:00:00Z"


def recon(facts, scopes=(), acts=(), config=StateConfig()):
    return StateReconstructor(facts, scopes, acts, config)


class ObservationTests(unittest.TestCase):
    def test_record_interpretation_per_category(self):
        cases = [
            (F("e", "analytics_1", category="ANALYTICS_PROFILE", user_id="user_2", email=None), EXPOSED),
            (F("e", "analytics_2", category="ANALYTICS_PROFILE", user_id=None, email=None, customer_reference=None), ANONYMIZED),
            (F("e", "tombstone_1", category="ACCOUNT_TOMBSTONE", subject_reference="user_2", name="Bob", email=None), EXPOSED),
            (F("e", "tombstone_2", category="ACCOUNT_TOMBSTONE", subject_reference="user_2", name=None, email=None, phone=None, email_hash=None), REDACTED),
            (F("e", "support_1", message_text="[REDACTED]"), REDACTED),
            (F("e", "support_2", message_text="Please send it to bob@x.com"), EXPOSED),
            (F("e", "support_3", requester_email=None, requester_name=None, requester_phone=None), REDACTED),
            (F("e", "data_flow_1", enabled="True"), ACTIVE),
            (F("e", "data_flow_2", enabled=False), DISABLED),
            (F("e", "backup_1", restore_allowed="1"), RESTORABLE),
            (F("e", "backup_2", restore_allowed="False"), BLOCKED),
            (F("e", "search_1"), PRESENT),
        ]
        for fact, kind in cases:
            self.assertEqual(interpret(fact).kind, kind, fact.resource_id)

    def test_log_events(self):
        cases = [
            (dict(event="connector_config_read_completed", enabled="true"), ACTIVE),
            (dict(event="connector_config_loaded", enabled="false"), DISABLED),
            (dict(event="restore_policy_read_completed", restore_allowed="false"), BLOCKED),
            (dict(event="restore_candidate_evaluated", eligible="true", deletion_block="false"), RESTORABLE),
            (dict(event="restore_authorization_completed", decision="deny"), BLOCKED),
            (dict(event="analytics_profile_read_completed", email="null", phone="null"), ANONYMIZED),
            (dict(event="analytics_profile_read_completed", email="a@b.co"), EXPOSED),
            (dict(event="message_pii_scan", detector="direct_identifier", matched_value="a@b.co"), EXPOSED),
            (dict(event="message_read", message_text="[REDACTED]"), REDACTED),
            (dict(event="export_metadata_read_completed", available="true"), PRESENT),
        ]
        for attrs, kind in cases:
            self.assertEqual(interpret(F("e", "search_9", state="OBSERVED", **attrs)).kind, kind, attrs)

    def test_explicit_states(self):
        self.assertEqual(interpret(F("e", "account_1", state="NOT_FOUND")).kind, ABSENT)
        self.assertEqual(interpret(F("e", "backup_1", state="ACTIVE")).kind, RESTORABLE)       # ACTIVE backup = restorable
        self.assertEqual(interpret(F("e", "data_flow_1", state="ACTIVE")).kind, ACTIVE)
        self.assertEqual(interpret(F("e", "support_4", state="IDENTIFIABLE")).kind, EXPOSED)


class CutoffTests(unittest.TestCase):
    def test_file_captured_after_audit_is_inadmissible_even_if_record_time_is_earlier(self):
        late = F("late", "search_1", captured="2026-09-04T00:00:00Z", observed="2026-09-02T00:00:00Z")
        self.assertEqual(recon([late]).reconstruct("search_1", AUDIT).kind, UNKNOWN)
        diag = recon([late], config=StateConfig(admissibility="record")).reconstruct("search_1", AUDIT)
        self.assertEqual((diag.kind, diag.evidence_id), (PRESENT, "late"))     # diagnostic mode only

    def test_record_after_audit_is_inadmissible_even_if_file_is_earlier_or_equal(self):
        f = F("e", "search_1", captured="2026-09-03T12:00:00Z", observed="2026-09-03T12:00:01Z")
        self.assertEqual(recon([f]).reconstruct("search_1", AUDIT).kind, UNKNOWN)

    def test_file_captured_exactly_at_audit_is_admissible(self):
        self.assertEqual(recon([F("e", "search_1", captured=AUDIT)]).reconstruct("search_1", AUDIT).kind, PRESENT)

    def test_later_evidence_does_not_change_the_answer(self):
        early = F("early", "search_1", state="NOT_FOUND", captured="2026-09-02T00:00:00Z")
        later = F("later", "search_1", captured="2026-09-09T00:00:00Z")
        st = recon([early, later]).reconstruct("search_1", AUDIT)
        self.assertEqual((st.kind, st.evidence_id), (ABSENT, "early"))

    def test_stale_observation_is_ignored_beyond_the_horizon(self):
        old = F("old", "search_1", captured="2026-06-01T00:00:00Z")
        st = recon([old]).reconstruct("search_1", AUDIT)
        self.assertEqual(st.kind, UNKNOWN)
        self.assertIn("stale_observations_ignored", st.flags)
        self.assertEqual(recon([old], config=StateConfig(horizon_days=365)).reconstruct("search_1", AUDIT).kind, PRESENT)


class ReconstructionTests(unittest.TestCase):
    def test_latest_observation_wins_and_recreation_is_flagged(self):
        a = F("a", "search_1", state="NOT_FOUND", captured="2026-09-01T00:00:00Z")
        b = F("b", "search_1", captured="2026-09-02T00:00:00Z")
        st = recon([a, b]).reconstruct("search_1", AUDIT)
        self.assertEqual((st.kind, st.evidence_id), (PRESENT, "b"))
        self.assertIn("recreated", st.flags)
        self.assertEqual([k for _, k, _ in st.history], [ABSENT, PRESENT])

    def test_same_time_disagreement_is_a_conflict_and_the_least_compliant_reading_is_used(self):
        a = F("a", "search_1", state="NOT_FOUND", captured="2026-09-02T00:00:00Z")
        b = F("b", "search_1", captured="2026-09-02T00:00:00Z")
        st = recon([a, b]).reconstruct("search_1", AUDIT)
        self.assertEqual(st.kind, PRESENT)
        self.assertIn("conflict", st.flags)

    def test_replica_drift_primary_clean_but_replica_still_holds_the_record(self):
        replica = F("db", "account_1", role="failover_replica", category="USER_PROFILE")
        scopes = [S("db", "account_service", "customer_profiles_primary", role="primary"),
                  S("db", "account_service", "customer_profiles_failover_replica", role="failover_replica")]
        st = recon([replica], scopes).reconstruct("account_1", AUDIT)
        self.assertEqual((st.kind, st.evidence_id), (PRESENT, "db"))
        self.assertIn("replica_drift", st.flags)
        self.assertTrue(any("absent on ['primary']" in n for n in st.notes))

    def test_later_snapshot_of_a_different_node_cannot_supersede_a_replica_record(self):
        replica = F("db", "account_1", role="failover_replica", category="USER_PROFILE")
        later_inventory = S("inv", "account_service", "customer_profiles", captured="2026-09-02T12:00:00Z", role="inventory")
        st = recon([replica], [S("db", "account_service", "customer_profiles_failover_replica", role="failover_replica"), later_inventory]
                   ).reconstruct("account_1", AUDIT)
        self.assertEqual(st.kind, PRESENT)

    def test_later_snapshot_of_the_same_node_supersedes_presence(self):
        first = F("db1", "account_1", role="inventory", category="USER_PROFILE", captured="2026-09-01T00:00:00Z")
        second_scope = S("db2", "account_service", "customer_profiles", captured="2026-09-02T00:00:00Z", role="inventory")
        st = recon([first], [second_scope]).reconstruct("account_1", AUDIT)
        self.assertEqual((st.kind, st.evidence_id, st.basis), (ABSENT, "db2", "scope_absence"))
        self.assertIn("superseded_by_absence", st.flags)

    def test_explicitly_enumerated_empty_collection_proves_absence_within_its_scope(self):
        scope = S("snap", "account_service", "customer_profiles", role="inventory")
        st = recon([], [scope]).reconstruct("account_9", AUDIT)
        self.assertEqual((st.kind, st.evidence_id, st.basis), (ABSENT, "snap", "scope_absence"))

    def test_absence_does_not_leak_across_systems_or_categories(self):
        wrong_system = S("snap", "search_service", "customer_search_documents", role="inventory")
        wrong_category = S("snap2", "support_platform", "support_conversations", role="primary")
        self.assertEqual(recon([], [wrong_system, wrong_category]).reconstruct("account_9", AUDIT).kind, UNKNOWN)

    def test_lookup_lists_and_ledgers_are_not_enumerations(self):
        self.assertEqual(recon([], [S("exp", "account_service", "profile_lookups")]).reconstruct("account_9", AUDIT).kind, UNKNOWN)

    def test_scope_captured_after_audit_or_stale_proves_nothing(self):
        late = S("late", "account_service", "customer_profiles", captured="2026-09-05T00:00:00Z", role="inventory")
        stale = S("old", "account_service", "customer_profiles", captured="2026-06-01T00:00:00Z", role="inventory")
        self.assertEqual(recon([], [late, stale]).reconstruct("account_9", AUDIT).kind, UNKNOWN)

    def test_subject_scoped_bundle_proves_absence_only_for_subjects_in_the_file(self):
        bundle = S("bundle", "account_service", "customer_profiles", subject_scoped=True)
        self.assertEqual(recon([], [bundle]).reconstruct("account_9", AUDIT).kind, UNKNOWN)
        self.assertEqual(recon([], [bundle]).reconstruct("account_9", AUDIT, subject_files={"other"}).kind, UNKNOWN)
        st = recon([], [bundle]).reconstruct("account_9", AUDIT, subject_files={"bundle"})
        self.assertEqual((st.kind, st.evidence_id), (ABSENT, "bundle"))

    def test_retained_categories_are_never_absent_by_silence(self):
        for rid, system, coll in (("audit_record_1", "security_service", "security_audit_records"),
                                  ("legal_hold_1", "legal_service", "retained_order_records"),
                                  ("backup_1", "backup_service", "backup_catalog"),
                                  ("tombstone_1", "identity_service", "deleted_customer_references")):
            st = recon([], [S("snap", system, coll, role="primary")]).reconstruct(rid, AUDIT)
            self.assertEqual(st.kind, UNKNOWN, rid)

    def test_explicit_not_found_is_absence_even_for_retained_categories(self):
        st = recon([F("e", "audit_record_1", state="NOT_FOUND")]).reconstruct("audit_record_1", AUDIT)
        self.assertEqual(st.kind, ABSENT)

    def test_workflow_claims_never_override_evidence(self):
        claim = Activity("activity_1", "2026-09-01T00:00:00Z", "DELETE_RECORD", "deletion_worker", "search_service", "search_1", "SUCCESS", "done")
        st = recon([F("e", "search_1")], acts=[claim]).reconstruct("search_1", AUDIT)
        self.assertEqual(st.kind, PRESENT)
        self.assertIn("claim_contradicted", st.flags)
        self.assertEqual(st.claims[0]["activity_id"], "activity_1")

    def test_claims_after_audit_are_not_even_listed(self):
        claim = Activity("activity_1", "2026-09-09T00:00:00Z", "DELETE_RECORD", "deletion_worker", "search_service", "search_1", "SUCCESS", "x")
        self.assertEqual(recon([F("e", "search_1")], acts=[claim]).reconstruct("search_1", AUDIT).claims, [])

    def test_provenance_is_kept(self):
        st = recon([F("e", "audit_record_1", captured="2026-09-02T00:00:00Z", observed="2026-09-02T01:00:00Z", retention_until="2031-01-01T00:00:00Z",
                      subject_reference="user_2")]).reconstruct("audit_record_1", AUDIT)
        self.assertEqual((st.evidence_id, st.observed_at, st.retention_until, st.decisive.fact.file_id),
                         ("e", "2026-09-02T01:00:00Z", "2031-01-01T00:00:00Z", "e"))


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.e = PolicyEngine.from_rules(RULES)
        self.ctx = Context(audit_at=AUDIT)

    def ops(self, cat, kind, **ctx):
        a = self.e.assess(cat, kind, Context(audit_at=AUDIT, **ctx))
        return (a.violation, a.acted, a.finding_op, a.remediation_op, a.verification_op)

    def test_delete_categories(self):
        for cat in ("USER_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY", "CACHE_ENTRY", "USER_EXPORT", "MARKETING_PROFILE"):
            with self.subTest(cat):
                self.assertEqual(self.ops(cat, PRESENT), (True, True, "REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"))
                self.assertEqual(self.ops(cat, ABSENT), (False, True, None, None, "VERIFY_RECORD_ABSENT"))

    def test_attachment_delete_only_when_it_belongs_only_to_the_subject(self):
        self.assertEqual(self.ops("SUPPORT_ATTACHMENT", PRESENT, sole_owner=True)[2:], ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"))
        self.assertEqual(self.ops("SUPPORT_ATTACHMENT", PRESENT, sole_owner=False)[:2], (False, False))
        self.assertEqual(self.ops("SUPPORT_ATTACHMENT", PRESENT)[0], True)

    def test_redact_categories_keep_the_record(self):
        for cat in ("SUPPORT_MESSAGE", "EMAIL_DELIVERY_LOG"):
            self.assertEqual(self.ops(cat, EXPOSED), (True, True, "REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"))
            self.assertEqual(self.ops(cat, REDACTED), (False, True, None, None, "VERIFY_RECORD_REDACTED"))
        # deleted although policy says redact: the spec has no finding for over-deletion, so no steps and a note
        a = self.e.assess("SUPPORT_MESSAGE", ABSENT, self.ctx)
        self.assertFalse(a.acted)
        self.assertTrue(a.notes)

    def test_anonymize_categories(self):
        for cat in ("ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"):
            self.assertEqual(self.ops(cat, EXPOSED), (True, True, "REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"))
            self.assertEqual(self.ops(cat, ANONYMIZED), (False, True, None, None, "VERIFY_RECORD_ANONYMIZED"))
        self.assertFalse(self.e.assess("ANALYTICS_PROFILE", PRESENT, self.ctx).acted)      # cannot tell: no invented policy

    def test_retained_audit_record_is_kept_not_deleted(self):
        self.assertEqual(self.ops("SECURITY_AUDIT_LOG", PRESENT, retention_until="2031-12-31T23:59:59Z"), (False, True, None, None, "VERIFY_RECORD_PRESENT"))
        a = self.e.assess("SECURITY_AUDIT_LOG", ABSENT, self.ctx)
        self.assertFalse(a.acted)                                    # missing retained record: no finding exists in the spec

    def test_expired_retention_period_is_not_given_invented_treatment(self):
        a = self.e.assess("SECURITY_AUDIT_LOG", PRESENT, Context(audit_at=AUDIT, retention_until="2026-01-01T00:00:00Z"))
        self.assertFalse(a.acted)
        self.assertTrue(any("retention period expired" in n for n in a.notes))

    def test_legal_hold_is_retained(self):
        self.assertEqual(self.ops("LEGAL_HOLD_RECORD", PRESENT, retention_until="2033-12-31T23:59:59Z"), (False, True, None, None, "VERIFY_RECORD_PRESENT"))
        self.assertFalse(self.e.assess("LEGAL_HOLD_RECORD", PRESENT, Context(audit_at=AUDIT, retention_until="2026-02-01T00:00:00Z")).acted)

    def test_backup_is_retained_with_restore_blocked(self):
        self.assertEqual(self.ops("BACKUP_ARCHIVE", RESTORABLE), (True, True, "REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"))
        self.assertEqual(self.ops("BACKUP_ARCHIVE", BLOCKED), (False, True, None, None, "VERIFY_RESTORE_BLOCKED"))
        self.assertNotIn("DELETE_RECORD", self.ops("BACKUP_ARCHIVE", RESTORABLE))

    def test_tombstone_keeps_only_the_stable_reference(self):
        self.assertEqual(self.ops("ACCOUNT_TOMBSTONE", EXPOSED), (True, True, "REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"))
        self.assertEqual(self.ops("ACCOUNT_TOMBSTONE", REDACTED), (False, True, None, None, "VERIFY_RECORD_REDACTED"))
        self.assertFalse(self.e.assess("ACCOUNT_TOMBSTONE", REDACTED, Context(audit_at=AUDIT, subject_reference_kept=False)).acted)
        self.assertFalse(self.e.assess("ACCOUNT_TOMBSTONE", ABSENT, self.ctx).acted)        # tombstone must be preserved, not deleted

    def test_data_flows_have_no_csv_row_but_must_be_disabled(self):
        self.assertNotIn("DATA_FLOW", self.e.rules)
        self.assertEqual(self.ops("DATA_FLOW", ACTIVE), (True, True, "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"))
        self.assertEqual(self.ops("DATA_FLOW", DISABLED), (False, True, None, None, "VERIFY_DATA_FLOW_DISABLED"))

    def test_required_action_is_read_from_the_rules_file(self):
        flipped = [Rule(r.rule_id, r.data_category, r.condition, "RETAIN" if r.data_category == "SEARCH_INDEX_ENTRY" else r.required_action) for r in RULES]
        a = PolicyEngine.from_rules(flipped).assess("SEARCH_INDEX_ENTRY", PRESENT, self.ctx)
        self.assertEqual((a.violation, a.verification_op), (False, "VERIFY_RECORD_PRESENT"))

    def test_unknown_inputs_get_no_invented_policy(self):
        self.assertFalse(self.e.assess(None, PRESENT, self.ctx).acted)
        self.assertFalse(self.e.assess("USER_PROFILE", UNKNOWN, self.ctx).acted)
        self.assertFalse(self.e.assess("SOMETHING_NEW", PRESENT, self.ctx).acted)

    def test_final_state_verification_per_category(self):
        expect = {"USER_PROFILE": "VERIFY_RECORD_ABSENT", "SUPPORT_MESSAGE": "VERIFY_RECORD_REDACTED", "ANALYTICS_PROFILE": "VERIFY_RECORD_ANONYMIZED",
                  "SECURITY_AUDIT_LOG": "VERIFY_RECORD_PRESENT", "BACKUP_ARCHIVE": "VERIFY_RESTORE_BLOCKED", "DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED"}
        for cat, op in expect.items():
            self.assertEqual(self.e.final_verification(cat), op, cat)


if __name__ == "__main__":
    unittest.main()
