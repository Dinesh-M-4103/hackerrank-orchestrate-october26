import unittest

from ghost_audit.identity import IdentityResolver, sha256_email
from ghost_audit.loaders import load_audit_log, load_identities, load_requests
from ghost_audit.models import IdentityRecord
from ghost_audit.text_ids import Identifiers, identifiers_in_text, identifiers_in_value

from .support import changes, identities, needs_dataset, resolver


class TextIdTests(unittest.TestCase):
    def test_emails_are_exact_tokens_and_lowercased(self):
        ids = identifiers_in_text("Contact: ANA.NEW@X.COM, or (bob@x.com).")
        self.assertEqual(ids.emails, frozenset({"ana.new@x.com", "bob@x.com"}))

    def test_lookalike_address_is_a_different_token(self):
        # 'xana.new@x.com' contains 'ana.new@x.com' as a substring but must never match it
        ids = identifiers_in_text("owner=xana.new@x.com and ana.new@x.com.au")
        self.assertNotIn("ana.new@x.com", ids.emails)
        self.assertEqual(ids.emails, frozenset({"xana.new@x.com", "ana.new@x.com.au"}))

    def test_user_refs_hashes_and_phones(self):
        h = sha256_email("bob@x.com")
        ids = identifiers_in_text(f"subject user_12 (not myuser_123) hash {h.upper()} tel +44 7700 900123")
        self.assertEqual(ids.user_refs, frozenset({"user_12"}))
        self.assertEqual(ids.hashes, frozenset({h}))
        self.assertEqual(ids.phones, frozenset({"+447700900123"}))

    def test_value_walker_reads_nested_json(self):
        ids = identifiers_in_value({"a": {"email": "A@B.CO"}, "l": [{"customer_reference": "user_5"}], "phone": "+1 (555) 010-0200"})
        self.assertEqual((ids.emails, ids.user_refs, ids.phones), (frozenset({"a@b.co"}), frozenset({"user_5"}), frozenset({"+15550100200"})))


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.r = resolver()

    def test_current_email(self):
        res = self.r.resolve_email("Ana.New@x.com")
        self.assertEqual((res.user_id, res.status), ("user_1", "CURRENT"))

    def test_historical_email_resolves_to_same_user_with_audit_trail(self):
        res = self.r.resolve_email("ana.old@x.com")
        self.assertEqual((res.user_id, res.status), ("user_1", "HISTORICAL"))
        self.assertEqual(res.audit_ids, ("audit_1",))

    def test_unknown_and_lookalike_emails_do_not_resolve(self):
        self.assertEqual(self.r.resolve_email("xana.new@x.com").status, "UNKNOWN")
        self.assertEqual(self.r.resolve_email("ana.new@x.com.au").status, "UNKNOWN")
        self.assertEqual(self.r.resolve_email("ana@x.com").status, "UNKNOWN")

    def test_shared_email_is_ambiguous_not_guessed(self):
        recs = identities() + [IdentityRecord("user_9", "Dup", "dup@x.com", "", "", "2026-01-01T00:00:00Z")]
        recs[1] = IdentityRecord("user_2", "Bob B", "dup@x.com", "+1 555 0200", sha256_email("dup@x.com"), "2026-01-01T00:00:00Z")
        res = IdentityResolver(recs, changes()).resolve_email("dup@x.com")
        self.assertEqual((res.user_id, res.status, res.candidates), (None, "AMBIGUOUS", ("user_2", "user_9")))

    def test_identifier_set_includes_historical_values_and_derived_hashes(self):
        ids = self.r.identifiers_for("user_1")
        self.assertEqual(ids.emails, frozenset({"ana.old@x.com", "ana.new@x.com"}))
        self.assertIn(sha256_email("ana.old@x.com"), ids.hashes)
        self.assertEqual(ids.phones, frozenset({"+15550099", "+15550100"}))

    def test_match_identifiers_by_email_hash_and_ref_but_not_phone_by_default(self):
        old_hash = sha256_email("ana.old@x.com")
        m = self.r.match_identifiers(Identifiers(hashes=frozenset({old_hash}), user_refs=frozenset({"user_2"}),
                                                 phones=frozenset({"+15550300"})))
        self.assertEqual(m, {"user_1": {"email_hash"}, "user_2": {"user_ref"}})
        self.assertEqual(self.r.match_identifiers(Identifiers(phones=frozenset({"+15550300"})), include_phones=True),
                         {"user_3": {"phone"}})

    def test_email_timeline_as_of(self):
        u = self.r.users["user_1"]
        self.assertEqual(u.email_at("2026-02-01T00:00:00Z"), "ana.old@x.com")
        self.assertEqual(u.email_at("2026-03-02T00:00:00Z"), "ana.new@x.com")


@needs_dataset
class DatasetIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = IdentityResolver(load_identities(), load_audit_log())

    def test_every_request_email_resolves_uniquely(self):
        for c in load_requests() + load_requests(sample=True):
            self.assertIn(self.r.resolve_email(c.email).status, ("CURRENT", "HISTORICAL"), c.case_id)

    def test_some_requests_use_historical_emails(self):
        hist = [c for c in load_requests() if self.r.resolve_email(c.email).status == "HISTORICAL"]
        self.assertGreater(len(hist), 50)

    def test_no_email_or_phone_is_shared_between_users(self):
        self.assertFalse([e for e, u in self.r._email_idx.items() if len(u) > 1])
        self.assertFalse([p for p, u in self.r._phone_idx.items() if len(u) > 1])

    def test_recorded_hash_equals_sha256_of_lowercased_email(self):
        for rec in load_identities():
            self.assertEqual(rec.email_hash, sha256_email(rec.email))


if __name__ == "__main__":
    unittest.main()
