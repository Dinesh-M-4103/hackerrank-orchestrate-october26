import unittest

from ghost_audit.attribution import AMBIGUOUS, CONFIRMED, NONE, PROBABLE, AccountAttributor, summarize
from ghost_audit.identity import sha256_email
from ghost_audit.intent_hints import nondeletion_hints
from ghost_audit.models import Activity, Fact
from ghost_audit.owners import build_owner_map
from ghost_audit.text_ids import Identifiers

from .support import case, link_activity, resolver, submit


def fact(file_id, rid, captured, emails=(), refs=(), hashes=(), refs_tokens=()):
    return Fact(file_id, captured, None, rid, None, "FOUND",
                Identifiers(frozenset(emails), frozenset(refs), frozenset(hashes)), refs=tuple(refs_tokens))


class OwnerMapTests(unittest.TestCase):
    def setUp(self):
        self.r = resolver()

    def test_links_by_current_historical_email_hash_and_user_ref(self):
        facts = [fact("e1", "account_1", "2026-09-01T00:00:00Z", emails=["ana.old@x.com"]),
                 fact("e2", "search_2", "2026-09-01T00:00:00Z", hashes=[sha256_email("bob@x.com")]),
                 fact("e3", "audit_record_3", "2026-09-01T00:00:00Z", refs=["user_3"])]
        om = build_owner_map(facts, [], self.r)
        self.assertEqual(om.owner_of("account_1"), "user_1")
        self.assertEqual(om.owner_of("search_2"), "user_2")
        self.assertEqual(om.owner_of("audit_record_3"), "user_3")
        self.assertEqual(om.links_for("account_1")[0].via, ("email",))

    def test_conflicting_identifiers_create_no_link(self):
        om = build_owner_map([fact("e1", "account_1", "2026-09-01T00:00:00Z", emails=["ana.new@x.com", "bob@x.com"])], [], self.r)
        self.assertIsNone(om.owner_of("account_1"))
        self.assertEqual(len(om.conflicts), 1)
        self.assertEqual(om.conflicts[0].users, ("user_1", "user_2"))

    def test_lookalike_email_creates_no_link_and_is_recorded_anonymous(self):
        om = build_owner_map([fact("e1", "account_1", "2026-09-01T00:00:00Z", emails=["xana.new@x.com"])], [], self.r)
        self.assertIsNone(om.owner_of("account_1"))
        self.assertEqual(om.anonymous["account_1"], ["e1"])

    def test_cutoff_hides_links_captured_after_as_of(self):
        om = build_owner_map([fact("late", "account_1", "2026-09-10T00:00:00Z", emails=["bob@x.com"])], [], self.r)
        self.assertEqual(om.owner_of("account_1", as_of="2026-09-09T00:00:00Z"), None)
        self.assertEqual(om.owner_of("account_1", as_of="2026-09-10T00:00:00Z"), "user_2")
        self.assertEqual(om.owner_of("account_1"), "user_2")

    def test_explicit_activity_link_and_alias_for_reference_only_document(self):
        acts = [link_activity("activity_9", "export_1842", "ana.old@x.com", "user_1", "2026-07-18T11:07:01Z", label="NST-2026-0718-1842")]
        doc = Fact("pdf1", "2026-07-18T11:00:00Z", None, None, None, "DOCUMENT", Identifiers(emails=frozenset({"ana.old@x.com"})),
                   refs=("NST-2026-0718-1842",))
        om = build_owner_map([doc], acts, self.r)
        kinds = sorted((l.kind, l.source_id) for l in om.links_for("export_1842"))
        self.assertEqual(kinds, [("ACTIVITY_LINK", "activity_9"), ("EVIDENCE", "pdf1")])
        self.assertEqual(om.owner_of("export_1842"), "user_1")

    def test_activity_link_that_mixes_users_is_a_conflict(self):
        acts = [link_activity("activity_9", "export_5", "ana.new@x.com", "user_2", "2026-07-18T11:07:01Z")]
        om = build_owner_map([], acts, self.r)
        self.assertIsNone(om.owner_of("export_5"))
        self.assertEqual(len(om.conflicts), 1)


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.r = resolver()

    def run_attr(self, cases, facts=(), acts=(), **kw):
        om = build_owner_map(facts, acts, self.r)
        return AccountAttributor(self.r, om, acts, **{k: v for k, v in kw.items() if k == "allow_elimination"}).attribute(
            cases, strict_cutoff=kw.get("strict_cutoff", True))

    # 1 ---------------------------------------------------------------- evidence first
    def test_confirmed_by_evidence_even_without_timing(self):
        res = self.run_attr([case("c1", "ana.old@x.com")], [fact("e1", "account_5", "2026-09-02T00:00:00Z", emails=["ana.new@x.com"])])
        a = res["c1"]
        self.assertEqual((a.status, a.basis, a.accounts, a.evidence_ids), (CONFIRMED, "owner_link", ("account_5",), ("e1",)))

    # 2 ---------------------------------------------------------------- unique timing
    def test_unique_timing_candidate_is_probable(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z")]       # 60 min before reported_at
        a = self.run_attr([case("c1", "bob@x.com")], acts=acts)["c1"]
        self.assertEqual((a.status, a.basis, a.accounts, a.evidence_ids), (PROBABLE, "timing_unique", ("account_7",), ("a1",)))

    # 3 ---------------------------------------------------------------- never pick among ambiguous candidates
    def test_two_cases_two_candidates_stay_ambiguous_with_candidates_preserved(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z"), submit("a2", "account_8", "2026-09-01T11:10:00Z")]
        res = self.run_attr([case("c1", "bob@x.com"), case("c2", "cy@x.com")], acts=acts)
        for cid in ("c1", "c2"):
            self.assertEqual((res[cid].status, res[cid].basis, res[cid].accounts), (AMBIGUOUS, "timing_ambiguous", ()))
            self.assertEqual(res[cid].candidates, ("account_7", "account_8"))
            self.assertEqual(res[cid].initial_candidates, ("account_7", "account_8"))

    def test_candidates_owned_by_someone_else_are_excluded(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z"), submit("a2", "account_8", "2026-09-01T11:10:00Z")]
        facts = [fact("e1", "account_7", "2026-09-02T00:00:00Z", emails=["cy@x.com"])]       # account_7 is cy's
        res = self.run_attr([case("c1", "bob@x.com"), case("c2", "cy@x.com")], facts, acts)
        self.assertEqual((res["c2"].status, res["c2"].accounts), (CONFIRMED, ("account_7",)))
        self.assertEqual((res["c1"].status, res["c1"].basis, res["c1"].accounts), (PROBABLE, "timing_unique", ("account_8",)))

    def test_two_cases_claiming_the_same_single_candidate_block_each_other(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z")]
        res = self.run_attr([case("c1", "bob@x.com"), case("c2", "cy@x.com")], acts=acts)
        for cid in ("c1", "c2"):
            self.assertEqual((res[cid].status, res[cid].basis, res[cid].accounts), (AMBIGUOUS, "timing_conflict", ()))

    def test_elimination_resolves_chained_cases_and_can_be_disabled(self):
        # c3 sees only account_7 (60 min); c4 sees both (30 and 90 min). Once c3 claims 7, c4 is left with 8.
        acts2 = [submit("a1", "account_7", "2026-09-01T11:00:00Z"), submit("a2", "account_8", "2026-09-01T10:00:00Z")]
        c3 = case("c3", "bob@x.com", reported_at="2026-09-01T12:00:00Z")           # lags: 60, 120 -> only account_7
        c4 = case("c4", "cy@x.com", reported_at="2026-09-01T11:30:00Z")            # lags: 30, 90  -> both in window
        res = self.run_attr([c3, c4], acts=acts2)
        self.assertEqual((res["c3"].accounts, res["c3"].basis), (("account_7",), "timing_unique"))
        self.assertEqual((res["c4"].status, res["c4"].accounts, res["c4"].basis), (PROBABLE, ("account_8",), "timing_elimination"))
        off = self.run_attr([c3, c4], acts=acts2, allow_elimination=False)
        self.assertEqual((off["c4"].status, off["c4"].accounts), (AMBIGUOUS, ()))

    # 4 ---------------------------------------------------------------- cutoffs
    def test_activity_after_audit_is_never_a_candidate(self):
        acts = [submit("a1", "account_7", "2026-09-04T11:00:00Z")]
        c = case("c1", "bob@x.com", reported_at="2026-09-04T12:00:00Z", audit_at="2026-09-03T12:00:00Z")
        self.assertEqual(self.run_attr([c], acts=acts)["c1"].status, NONE)

    def test_timing_outside_documented_window_is_not_a_candidate(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:50:00Z"), submit("a2", "account_8", "2026-09-01T08:00:00Z")]  # 10 and 240 min
        self.assertEqual(self.run_attr([case("c1", "bob@x.com")], acts=acts)["c1"].status, NONE)

    def test_ownership_evidence_captured_after_audit_is_not_used_when_strict(self):
        late = [fact("late", "account_5", "2026-09-20T00:00:00Z", emails=["bob@x.com"])]
        self.assertEqual(self.run_attr([case("c1", "bob@x.com")], late)["c1"].status, NONE)
        diag = self.run_attr([case("c1", "bob@x.com")], late, strict_cutoff=False)["c1"]
        self.assertEqual((diag.status, diag.accounts), (CONFIRMED, ("account_5",)))

    def test_post_audit_ownership_does_not_exclude_a_candidate_when_strict(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z"), submit("a2", "account_8", "2026-09-01T11:10:00Z")]
        late = [fact("late", "account_7", "2026-09-20T00:00:00Z", emails=["cy@x.com"])]
        res = self.run_attr([case("c1", "bob@x.com")], late, acts)
        self.assertEqual((res["c1"].status, res["c1"].candidates), (AMBIGUOUS, ("account_7", "account_8")))

    # 5 ---------------------------------------------------------------- multi-account users and unknown users
    def test_multiple_linked_accounts_are_disambiguated_only_by_timing(self):
        facts = [fact("e1", "account_5", "2026-03-10T00:00:00Z", emails=["ana.old@x.com"]),
                 fact("e2", "account_6", "2026-08-30T00:00:00Z", emails=["ana.new@x.com"])]
        acts = [submit("a1", "account_6", "2026-09-01T11:00:00Z")]
        a = self.run_attr([case("c1", "ana.old@x.com")], facts, acts)["c1"]
        self.assertEqual((a.status, a.basis, a.accounts, a.candidates), (CONFIRMED, "owner_link_timing_disambiguated", ("account_6",), ("account_5", "account_6")))
        none = self.run_attr([case("c1", "ana.old@x.com")], facts)["c1"]
        self.assertEqual((none.accounts, none.candidates), (("account_5", "account_6"), ("account_5", "account_6")))

    def test_unknown_email_is_none(self):
        a = self.run_attr([case("c1", "nobody@x.com")])["c1"]
        self.assertEqual((a.status, a.basis, a.user_id), (NONE, "user_unresolved", None))

    def test_summary_counts(self):
        acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z"), submit("a2", "account_8", "2026-09-01T11:10:00Z")]
        s = summarize(self.run_attr([case("c1", "bob@x.com"), case("c2", "cy@x.com")], acts=acts))
        self.assertEqual((s["by_status"], s["ambiguous_candidate_set_sizes"]), ({AMBIGUOUS: 2}, {2: 2}))


class IntentHintTests(unittest.TestCase):
    def test_classify_row_hints_only_when_resource_is_owned_by_the_person_before_audit(self):
        r = resolver()
        classify = Activity("a9", "2026-08-28T11:07:25Z", "CLASSIFY_REQUEST", "privacy_intake", "privacy_case_management", "account_2461",
                            "SUCCESS", "Privacy intake classified request for account_2461 as non-deletion; no destructive action was authorized.")
        om = build_owner_map([fact("e1", "account_2461", "2026-08-29T00:00:00Z", emails=["bob@x.com"])], [], r)
        c = case("c1", "bob@x.com", audit_at="2026-09-03T12:00:00Z")
        self.assertEqual(nondeletion_hints([c], [classify], om, r), {"c1": ["a9"]})
        early = case("c2", "bob@x.com", audit_at="2026-08-28T12:00:00Z")      # ownership evidence not yet captured
        self.assertEqual(nondeletion_hints([early], [classify], om, r), {})
        self.assertEqual(nondeletion_hints([early], [classify], om, r, strict_cutoff=False), {"c2": ["a9"]})


if __name__ == "__main__":
    unittest.main()
