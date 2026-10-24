import json
import unittest

from ghost_audit.extractors import extract_file
from ghost_audit.extractors.logs import parse_log_line
from ghost_audit.extractors.sql_snapshot import table_role
from ghost_audit.loaders import load_manifest
from ghost_audit.extractors import extract_all

from .support import TempDirCase, minimal_pdf, needs_dataset

SQL_REPLICAS = """-- Database topology: regional_replicas
-- Captured at: 2026-05-19T06:00:00Z
BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO "customer_data_primary" VALUES('search_1','search_service','customer_search_documents','{"resource_id":"search_1","document":{"email":"bob@x.com"}}','2026-05-19T05:00:00Z');
CREATE TABLE customer_data_replica_ap_south (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO "customer_data_replica_ap_south" VALUES('account_9','account_service','customer_profiles','{"resource_id":"account_9","customer_reference":"user_2","email":"BOB@X.COM","phone":"+1 555 0200"}','2026-05-19T05:00:00Z');
INSERT INTO "customer_data_replica_ap_south" VALUES('search_1','search_service','customer_search_documents','{"resource_id":"search_1","document":{"email":"bob@x.com"}}','2026-05-19T05:00:00Z');
COMMIT;
"""

SQL_INVENTORY = """BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
INSERT INTO "snapshot_scope" VALUES('account_service','2026-09-16T08:37:39Z');
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT, full_name TEXT, email TEXT, phone TEXT, email_hash TEXT, account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE support_records (resource_id TEXT PRIMARY KEY, customer_reference TEXT, requester_name TEXT, requester_email TEXT, requester_phone TEXT, message_excerpt TEXT, updated_at TEXT NOT NULL);
INSERT INTO "support_records" VALUES('support_4',NULL,NULL,'CY@X.COM',NULL,'hello','2026-09-16T06:00:00Z');
COMMIT;
"""


class SqlTests(TempDirCase):
    def test_replica_drift_is_visible_through_source_roles(self):
        res = extract_file(self.meta("db/replicas.sql", SQL_REPLICAS, "evidence_sql"))
        self.assertEqual(res.kind, "sql_snapshot:regional_replicas")
        roles = {(f.resource_id, f.source_role) for f in res.facts}
        self.assertIn(("account_9", "replica_ap_south"), roles)
        self.assertNotIn(("account_9", "primary"), roles)          # present only on the replica = drift
        self.assertIn(("search_1", "primary"), roles)
        acct = next(f for f in res.facts if f.resource_id == "account_9")
        self.assertEqual((acct.system_id, acct.category), ("account_service", "USER_PROFILE"))
        self.assertEqual(acct.identifiers.emails, frozenset({"bob@x.com"}))
        self.assertEqual(acct.identifiers.user_refs, frozenset({"user_2"}))
        self.assertEqual(res.lines_parsed, res.lines_total)

    def test_inventory_layout_scope_and_empty_table(self):
        res = extract_file(self.meta("db/inv.sql", SQL_INVENTORY, "evidence_inv"))
        self.assertTrue(any(s.system_id == "account_service" and s.collection is None for s in res.scopes))
        # the empty customer_profiles table still enumerates the collection, so absence is provable from it
        self.assertTrue(any(s.collection == "customer_profiles" for s in res.scopes))
        sup = next(f for f in res.facts if f.resource_id == "support_4")
        self.assertEqual(sup.identifiers.emails, frozenset({"cy@x.com"}))
        self.assertEqual(sup.category, "SUPPORT_MESSAGE")

    def test_bad_sql_is_reported_not_raised(self):
        res = extract_file(self.meta("db/bad.sql", "CREATE TABLE x (", "evidence_bad"))
        self.assertTrue(res.warnings or res.error)

    def test_table_roles(self):
        self.assertEqual(table_role("customer_data_primary"), "primary")
        self.assertEqual(table_role("customer_data_read_replica"), "read_replica")
        self.assertEqual(table_role("customer_profiles_failover_replica"), "failover_replica")
        self.assertEqual(table_role("customer_profiles_primary"), "primary")
        self.assertEqual(table_role("support_records"), "inventory")


class PdfTests(TempDirCase):
    def test_pdf_text_identifiers_and_attachment_category(self):
        pdf = minimal_pdf(["DELIVERY ADDRESS CORRECTION REQUEST", "Support reference CS-4827 / support_001",
                           "Detail ana.new@x.com", "Attachment properties and upload history"])
        res = extract_file(self.meta("documents/att.pdf", pdf, "evidence_pdf"))
        self.assertEqual(res.kind, "pdf")
        self.assertEqual(len(res.facts), 1)
        f = res.facts[0]
        self.assertEqual((f.resource_id, f.category, f.state), ("support_001", "SUPPORT_ATTACHMENT", "FOUND"))
        self.assertEqual(f.identifiers.emails, frozenset({"ana.new@x.com"}))
        self.assertIn("CS-4827", f.refs)

    def test_retention_register_rows(self):
        pdf = minimal_pdf(["REGISTER", "1 ORD-20264118 | user_2 | Card dispute | 04 May 2026 | Retain until 04 May 2033 | AR"])
        res = extract_file(self.meta("documents/reg.pdf", pdf, "evidence_reg"))
        self.assertEqual(len(res.facts), 1)
        self.assertEqual((res.facts[0].category, res.facts[0].identifiers.user_refs, res.facts[0].refs),
                         ("LEGAL_HOLD_RECORD", frozenset({"user_2"}), ("ORD-20264118",)))

    def test_reference_only_document(self):
        pdf = minimal_pdf(["CUSTOMER STATEMENT", "Statement NST-2026-0718-1842", "user_1", "ana.new@x.com"])
        res = extract_file(self.meta("documents/st.pdf", pdf, "evidence_st"))
        f = res.facts[0]
        self.assertIsNone(f.resource_id)
        self.assertEqual(f.refs, ("NST-2026-0718-1842",))


class JsonTests(TempDirCase):
    def test_service_export_404_has_no_identity_and_search_hit_merges(self):
        doc = {"exported_at": "2026-09-15T11:44:55Z", "region": "global", "service_exports": {
            "account_service": {"profile_lookups": [
                {"observed_at": "2026-09-15T09:31:40Z", "profile": None, "resource_id": "account_3136", "status_code": 404},
                {"observed_at": "2026-09-15T09:41:33Z", "profile": {"email": "Bob@X.com"}, "resource_id": "account_5334", "status_code": 200}]},
            "search_service": {"index_queries": [{"observed_at": "2026-09-15T09:00:00Z", "query": "cy@x.com", "resource_id": "search_9",
                                                  "hits": {"documents": [{"resource_id": "search_9", "email": "cy@x.com"}]}}]}}}
        res = extract_file(self.meta("records/exp.json", json.dumps(doc), "evidence_exp"))
        by = {f.resource_id: f for f in res.facts}
        self.assertEqual(len(res.facts), 3)                       # nested hit document did not create a fourth fact
        self.assertEqual((by["account_3136"].state, by["account_3136"].identifiers.is_empty()), ("NOT_FOUND", True))
        self.assertEqual((by["account_5334"].state, by["account_5334"].identifiers.emails), ("FOUND", frozenset({"bob@x.com"})))
        self.assertEqual(by["search_9"].observed_at, "2026-09-15T09:00:00Z")
        self.assertTrue(any(s.system_id == "account_service" and s.collection == "profile_lookups" for s in res.scopes))

    def test_bundle_snapshot_scope_survives_empty_records(self):
        doc = {"exported_at": "2026-09-17T12:00:00Z", "source": "s", "snapshots": [
            {"captured_at": "2026-09-15T12:00:00Z", "data_store": "account_service", "collection": "customer_profiles", "records": []},
            {"captured_at": "2026-09-15T12:00:00Z", "data_store": "identity_service", "collection": "deleted_customer_references",
             "records": [{"resource_id": "tombstone_4", "subject_reference": "user_2", "name": None, "email": None}]}]}
        res = extract_file(self.meta("records/b.json", json.dumps(doc), "evidence_b"))
        self.assertEqual(len(res.facts), 1)
        self.assertEqual(res.facts[0].category, "ACCOUNT_TOMBSTONE")
        self.assertEqual(res.facts[0].attrs["email"], None)
        self.assertEqual(len(res.scopes), 2)

    def test_ledger_state_comes_from_result(self):
        doc = {"exported_at": "2026-10-01T12:00:00Z", "origin": "o", "record_count": 1, "records": [
            {"recorded_at": "2026-10-01T12:00:00Z", "system": "search_service", "resource_id": "search_2", "record_type": "SEARCH_INDEX_ENTRY",
             "result": "FOUND", "subject_reference": "CY@X.COM"}]}
        res = extract_file(self.meta("records/l.json", json.dumps(doc), "evidence_l"))
        f = res.facts[0]
        self.assertEqual((f.state, f.category, f.system_id), ("FOUND", "SEARCH_INDEX_ENTRY", "search_service"))


class HandoverTests(TempDirCase):
    def test_pipe_rows(self):
        txt = ("CUSTOMER PLATFORM - SHIFT HANDOVER\nSnapshot: 2026-10-01T12:00:00Z\n\n"
               "Account and downstream checks completed during the shift:\n\n"
               "- 2026-10-01T12:00:00Z | account_service | account_4248 | USER_PROFILE | NOT_FOUND | NOVAK@X.COM\n"
               "- 2026-10-01T12:00:00Z | security_service | audit_record_9394 | SECURITY_AUDIT_LOG | FOUND | user_32\n")
        res = extract_file(self.meta("records/h.txt", txt, "evidence_h"))
        self.assertEqual(len(res.facts), 2)
        self.assertEqual(res.facts[0].identifiers.emails, frozenset({"novak@x.com"}))
        self.assertEqual((res.facts[1].category, res.facts[1].identifiers.user_refs), ("SECURITY_AUDIT_LOG", frozenset({"user_32"})))
        self.assertEqual(res.lines_parsed, res.lines_total)


LOG_DIALECTS = [
    ('2026-09-15T11:51:09Z level=DEBUG service=account_service trace_id=tr-1 event=profile_lookup_started lookup_key="ana.old@x.com" attempt=1\n'
     '2026-09-15T11:51:19Z level=INFO service=account_service trace_id=tr-1 resource_id=account_100 event=profile_lookup_completed rows=0 http_status=404\n',
     "account_100", "NOT_FOUND", "ana.old@x.com"),
    ('2026-09-10T08:56:17Z {"severity":"DEBUG","service":"search_service","trace_id":"tr-2","event":"index_query_started","lookup_key":"BOB@X.COM"}\n'
     '2026-09-10T08:56:20Z {"severity":"INFO","service":"search_service","trace_id":"tr-2","resource_id":"search_5","event":"index_query_completed","result":"hit"}\n',
     "search_5", "FOUND", "bob@x.com"),
    ('2026-07-03T07:51:30Z | DEBUG | analytics_platform | tr-3 | analytics_profile_scan_started | lookup_key="cy@x.com" attempt="1"\n'
     '2026-07-03T07:51:32Z | INFO | analytics_platform | tr-3 | analytics_profile_read_completed | resource_id="analytics_9" rows="1"\n',
     "analytics_9", "FOUND", "cy@x.com"),
    ('2026-07-28T05:51:18Z [DEBUG] [account_service] [tr-4] profile_lookup_started: lookup_key="ana.new@x.com" attempt="1"\n'
     '2026-07-28T05:51:21Z [INFO] [account_service] [tr-4] profile_lookup_completed: resource_id="account_7" rows="0" http_status="404"\n',
     "account_7", "NOT_FOUND", "ana.new@x.com"),
    ('2026-08-22T10:51:27Z <15>1 node-3 support_platform tr-5 - - level=DEBUG event="attachment_scan_started" scan_key="bob@x.com"\n'
     '2026-08-22T10:51:31Z <15>1 node-3 support_platform tr-5 - - level=DEBUG resource_id="support_9" event="attachment_pii_scan" rows="1"\n',
     "support_9", "FOUND", "bob@x.com"),
    ('2026-06-08T06:52:09Z requestId=tr-6 level=INFO function=search_service message="resource_id=\\"search_77\\" event=\\"index_query_completed\\" result=\\"hit\\" correlation_key=\\"ana.old@x.com\\""\n',
     "search_77", "FOUND", "ana.old@x.com"),
]


class LogTests(TempDirCase):
    def test_every_dialect_parses_and_links_email_to_resource(self):
        for i, (text, rid, state, email) in enumerate(LOG_DIALECTS):
            with self.subTest(dialect=i):
                res = extract_file(self.meta(f"logs/d{i}.log", text, f"evidence_log{i}"))
                self.assertEqual(res.lines_parsed, res.lines_total, res.warnings)
                f = next(x for x in res.facts if x.resource_id == rid)
                self.assertEqual(f.state, state)
                self.assertIn(email, f.identifiers.emails)       # tied through the trace / inline key
                self.assertIsNotNone(f.observed_at)

    def test_unparseable_line_is_reported(self):
        res = extract_file(self.meta("logs/junk.log", "not a log line\n", "evidence_junk"))
        self.assertEqual((res.lines_total, res.lines_parsed), (1, 0))

    def test_restore_state_from_log_event(self):
        l = parse_log_line('2026-09-15T11:54:41Z level=ERROR service=backup_service trace_id=tr-9 resource_id=backup_3245 event=restore_candidate_evaluated eligible=true deletion_block=false')
        from ghost_audit.extractors.logs import derive_log_state
        self.assertEqual(derive_log_state(l.attrs), "RESTORABLE")


@needs_dataset
class RealEvidenceTests(unittest.TestCase):
    def test_every_non_image_file_extracts_cleanly(self):
        manifest = load_manifest()
        res = extract_all(manifest)
        for m in manifest:
            r = res[m.file_id]
            if r.unsupported:
                self.assertEqual(m.suffix, ".png", m.file_id)
                continue
            self.assertIsNone(r.error, m.file_id)
            self.assertEqual((r.lines_parsed, r.warnings), (r.lines_total, []), m.file_id)
            self.assertTrue(r.facts or r.scopes, m.file_id)

    def test_real_pdf_extraction(self):
        r = extract_all([m for m in load_manifest() if m.suffix == ".pdf"])
        facts = [f for x in r.values() for f in x.facts]
        self.assertTrue(any(f.resource_id == "support_001" and f.category == "SUPPORT_ATTACHMENT" for f in facts))
        self.assertTrue(any(f.category == "LEGAL_HOLD_RECORD" and "user_17" in f.identifiers.user_refs for f in facts))


if __name__ == "__main__":
    unittest.main()
