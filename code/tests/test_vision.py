import json
import subprocess
import sys
import unittest
from pathlib import Path

from ghost_audit.loaders import load_manifest, load_requests
from ghost_audit.owners import build_owner_map
from ghost_audit.paths import CODE_DIR
from ghost_audit.vision.client import (AuthError, BudgetExceeded, FakeVisionClient, InvalidRequestError, ModelResponse, PROMPT, RateLimitError,
                                       ResilientClient, TimeoutError_, TransientError, UsageTracker, VisionDisabled, build_real_client,
                                       classify_api_error, image_key)
from ghost_audit.vision.config import ENABLE_ENV, KEY_ENV, VisionConfig
from ghost_audit.vision.estimate import estimate, image_tokens
from ghost_audit.vision.facts import to_extraction
from ghost_audit.vision.fixtures import fake_client_from_fixture
from ghost_audit.vision.interpreter import ScreenshotInterpreter
from ghost_audit.vision.schema import (DISABLED, ERROR, MALFORMED, OK, UNCERTAIN, UNREADABLE, RESPONSE_JSON_SCHEMA, parse_interpretation)
from ghost_audit.vision.timeparse import latest_displayed, parse_display_time

from .support import TempDirCase, deletion_case, make_pipeline, needs_dataset, resolver

FIXTURE = CODE_DIR / "evaluation" / "screenshot_transcriptions.json"
PNG = b"\x89PNG\r\n\x1a\n"


def row(text, rids=(), emails=(), refs=(), phones=(), status=""):
    return {"text": text, "resource_ids": list(rids), "emails": list(emails), "user_refs": list(refs), "phones": list(phones), "status_text": status}


def good(**kw):
    base = {"readable": True, "application": "Search Console", "page_title": "Customer Search", "visible_text_lines": ["search_9 bob@x.com user_2"],
            "identifiers": {"resource_ids": ["search_9"], "emails": ["bob@x.com"], "user_refs": ["user_2"], "phones": [], "reference_codes": []},
            "displayed_times": [{"text": "01 Sep 2026 06:00:14 UTC", "meaning": "executed"}],
            "rows": [row("search_9 | bob@x.com | user_2", ["search_9"], ["bob@x.com"], ["user_2"])],
            "possible_links": [{"resource_id": "search_9", "subject": "bob@x.com", "basis": "same_row", "quote": "search_9 | bob@x.com | user_2"}],
            "confidence": 0.9, "ambiguities": [], "rationale": "one hit"}
    base.update(kw)
    return base


CFG = VisionConfig(enabled=True, min_interval_s=0.0, base_delay_s=2.0, max_delay_s=30.0, max_attempts=3)


class VisionCase(TempDirCase):
    def image(self, name="a.png", payload=b"x", file_id="evidence_i", captured="2026-09-01T06:00:00Z"):
        return self.meta(f"images/{name}", PNG + payload, file_id, captured)

    def interpreter(self, outcomes, config=CFG, **kw):
        sleeps = []
        client = FakeVisionClient(outcomes)
        return ScreenshotInterpreter(client, config, sleep=sleeps.append, **kw), client, sleeps

    @staticmethod
    def key(meta):
        return image_key(Path(meta.path).read_bytes())


class ResponseValidationTests(VisionCase):
    def test_valid_response_is_interpreted_with_provenance_from_the_manifest(self):
        m = self.image()
        it, client, _ = self.interpreter({self.key(m): json.dumps(good())})
        r = it.interpret(m)
        self.assertEqual((r.status, r.file_id, r.path, r.captured_at), (OK, "evidence_i", "evidence/images/a.png", "2026-09-01T06:00:00Z"))
        self.assertTrue(all(l.supported for l in r.links))
        self.assertEqual(r.rows[0].resource_ids, ["search_9"])
        self.assertEqual(len(client.calls), 1)

    def test_malformed_responses_become_structured_results_not_exceptions(self):
        for raw in ("not json at all", "[1, 2]", json.dumps({"readable": True, "identifiers": "nope"}), ""):
            r = parse_interpretation(raw, file_id="e", path="p", captured_at="2026-09-01T06:00:00Z")
            self.assertEqual(r.status, MALFORMED if raw != "" else MALFORMED, raw)
            self.assertTrue(r.problems)

    def test_unreadable_image(self):
        r = parse_interpretation(json.dumps(good(readable=False, ambiguities=["blurred"])), file_id="e", path="p", captured_at="2026-09-01T06:00:00Z")
        self.assertEqual((r.status, r.ambiguities), (UNREADABLE, ["blurred"]))
        ex = to_extraction(self.image(), r)
        self.assertTrue(ex.unsupported and not ex.facts)

    def test_wrongly_shaped_identifiers_are_discarded(self):
        data = good(identifiers={"resource_ids": ["search_9", "banana_5"], "emails": ["bob@x.com", "not-an-email"], "user_refs": ["user_2", "u2"], "phones": [], "reference_codes": []})
        r = parse_interpretation(json.dumps(data), file_id="e", path="p", captured_at="2026-09-01T06:00:00Z")
        self.assertEqual((r.identifiers["resource_ids"], r.identifiers["emails"], r.identifiers["user_refs"]), (["search_9"], ["bob@x.com"], ["user_2"]))
        self.assertEqual(len(r.discarded), 3)

    def test_only_visibly_grounded_links_are_supported(self):
        links = [
            {"resource_id": "search_9", "subject": "bob@x.com", "basis": "same_row", "quote": "search_9 | bob@x.com"},          # supported
            {"resource_id": "search_9", "subject": "user_2", "basis": "filename", "quote": "search_9 user_2"},                   # basis
            {"resource_id": "search_9", "subject": "user_2", "basis": "same_row", "quote": "search_9 only"},                     # quote lacks subject
            {"resource_id": "search_9", "subject": "user_2", "basis": "same_row", "quote": ""},                                 # no quote
            {"resource_id": "search_9", "subject": "Gabriel Silva", "basis": "same_row", "quote": "search_9 Gabriel Silva"},     # not an identifier
            {"resource_id": "nonsense", "subject": "bob@x.com", "basis": "same_row", "quote": "nonsense bob@x.com"},           # unknown resource shape
        ]
        r = parse_interpretation(json.dumps(good(possible_links=links)), file_id="e", path="p", captured_at="2026-09-01T06:00:00Z")
        self.assertEqual([l.supported for l in r.links], [True, False, False, False, False, False])
        self.assertTrue(all(l.why_not for l in r.links if not l.supported))

    def test_contradictory_subjects_make_the_result_uncertain_and_yield_no_facts(self):
        rows = [row("search_9 | bob@x.com", ["search_9"], ["bob@x.com"]), row("search_9 | cy@x.com", ["search_9"], ["cy@x.com"])]
        r = parse_interpretation(json.dumps(good(rows=rows)), file_id="e", path="p", captured_at="2026-09-01T06:00:00Z")
        self.assertEqual(r.status, UNCERTAIN)
        self.assertIn("contradictory", " ".join(r.problems))
        self.assertTrue(to_extraction(self.image(), r).unsupported)

    def test_link_that_contradicts_the_rows_is_uncertain(self):
        links = [{"resource_id": "search_9", "subject": "cy@x.com", "basis": "same_row", "quote": "search_9 cy@x.com"}]
        r = parse_interpretation(json.dumps(good(possible_links=links)), file_id="e", path="p", captured_at="2026-09-01T06:00:00Z")
        self.assertEqual(r.status, UNCERTAIN)

    def test_low_confidence_is_uncertain(self):
        m = self.image()
        it, _, _ = self.interpreter({self.key(m): json.dumps(good(confidence=0.2))})
        self.assertEqual(it.interpret(m).status, UNCERTAIN)

    def test_schema_describes_only_observation_fields(self):
        props = set(RESPONSE_JSON_SCHEMA["properties"])
        self.assertFalse(props & {"state", "status", "deletion_status", "operation", "evidence_id", "policy", "step_no"})


class NoUnsupportedEvidenceTests(VisionCase):
    def test_model_supplied_ids_states_and_actions_are_discarded(self):
        data = good(evidence_id="evidence_99", activity_id="activity_1", audit_id="audit_1", state="ABSENT", deletion_status="COMPLETE",
                    operation="DELETE_RECORD")
        r = parse_interpretation(json.dumps(data), file_id="evidence_i", path="p", captured_at="2026-09-01T06:00:00Z")
        self.assertEqual(r.status, OK)
        self.assertEqual(len(r.discarded), 6)
        m = self.image()
        ex = to_extraction(m, r)
        self.assertTrue(ex.facts)
        self.assertEqual({f.file_id for f in ex.facts}, {"evidence_i"})            # provenance is the manifest's, never the model's
        for f in ex.facts:
            self.assertNotIn("evidence_99", json.dumps(f.attrs))
            self.assertEqual(f.state, "PRESENT")

    def test_model_cannot_change_status_or_steps(self):
        base = [__import__("tests.support", fromlist=["F"]).F("e1", "audit_record_1", refs=["user_2"], retention_until="2031-01-01T00:00:00Z")]
        plain = make_pipeline(base).run([deletion_case()])["c1"]
        data = good(deletion_status="INCOMPLETE", operation="DELETE_RECORD", evidence_id="evidence_99")
        m = self.image()
        extra = to_extraction(m, parse_interpretation(json.dumps(data), file_id="evidence_i", path="p", captured_at=m.captured_at)).facts
        poisoned = make_pipeline(base + extra).run([deletion_case()])["c1"]
        self.assertEqual(poisoned.status, "INCOMPLETE")        # only because the screenshot visibly shows a search entry for bob; not because the model said so
        self.assertNotIn("evidence_99", poisoned.to_row()["steps_json"])
        self.assertEqual(plain.status, "COMPLETE")

    def test_row_without_a_supported_link_is_an_anonymous_observation(self):
        data = good(possible_links=[])
        m = self.image()
        f = to_extraction(m, parse_interpretation(json.dumps(data), file_id="evidence_i", path="p", captured_at=m.captured_at)).facts
        self.assertEqual([x.identifiers.is_empty() for x in f], [True])
        self.assertIsNone(build_owner_map(f, [], resolver()).owner_of("search_9"))

    def test_file_names_and_resource_less_rows_create_no_facts(self):
        data = good(identifiers={"resource_ids": [], "emails": [], "user_refs": [], "phones": [], "reference_codes": []},
                    rows=[row("delivery_confirmation_19.pdf | 684 KB | v3")], possible_links=[
                        {"resource_id": "object_19", "subject": "user_2", "basis": "filename", "quote": "delivery_confirmation_19.pdf"}])
        m = self.image()
        ex = to_extraction(m, parse_interpretation(json.dumps(data), file_id="evidence_i", path="p", captured_at=m.captured_at))
        self.assertEqual(ex.facts, [])
        self.assertTrue(any("unsupported link hypothesis" in w for w in ex.warnings))


class CutoffTests(VisionCase):
    def facts_for(self, data, captured):
        m = self.image(captured=captured)
        it = parse_interpretation(json.dumps(data), file_id="evidence_i", path=m.rel_path, captured_at=m.captured_at)
        return to_extraction(m, it)

    def test_screenshot_captured_after_audit_cannot_establish_state_or_ownership(self):
        ex = self.facts_for(good(displayed_times=[]), "2026-09-05T00:00:00Z")
        plan = make_pipeline(ex.facts).run([deletion_case(audit_at="2026-09-03T12:00:00Z")])["c1"]
        self.assertEqual(plan.steps, [])
        self.assertIn("insufficient_evidence", plan.flags)
        later = make_pipeline(ex.facts).run([deletion_case(audit_at="2026-09-06T12:00:00Z")])["c1"]
        self.assertIn(("REPORT_REMAINING_RECORD", "search_9"), [(s["operation"], s["resource_id"]) for s in later.steps])

    def test_a_row_displaying_a_time_after_audit_is_inadmissible_but_other_rows_of_the_same_screenshot_are_not(self):
        late_row = row("search_8 | cy@x.com | 04 Sep 10:00 UTC", ["search_8"], ["cy@x.com"])         # dated after the 03 Sep audit
        early_row = row("search_9 | bob@x.com | user_2 | 02 Sep 08:00 UTC", ["search_9"], ["bob@x.com"], ["user_2"])
        links = [{"resource_id": "search_8", "subject": "cy@x.com", "basis": "same_row", "quote": "search_8 | cy@x.com"},
                 {"resource_id": "search_9", "subject": "bob@x.com", "basis": "same_row", "quote": "search_9 | bob@x.com | user_2"}]
        data = good(identifiers={"resource_ids": ["search_8", "search_9"], "emails": ["bob@x.com", "cy@x.com"], "user_refs": ["user_2"], "phones": [],
                                 "reference_codes": []}, rows=[late_row, early_row], possible_links=links,
                    displayed_times=[{"text": "04 Sep 2026 10:00 UTC", "meaning": "newest row"}])
        ex = self.facts_for(data, "2026-09-02T09:00:00Z")
        by = {f.resource_id: f for f in ex.facts}
        self.assertTrue(any("temporal_conflict" in w for w in ex.warnings))                      # the page is flagged ...
        self.assertEqual(by["search_8"].observed_at, "2026-09-04T10:00:00Z")                   # ... the late row is only valid from its own time
        self.assertIsNone(by["search_9"].observed_at)                                            # ... the early row keeps the manifest capture time
        audit = "2026-09-03T12:00:00Z"
        self.assertFalse(by["search_8"].available_at(audit))
        self.assertTrue(by["search_9"].available_at(audit))
        plan = make_pipeline(ex.facts).run([deletion_case(audit_at=audit)])["c1"]
        self.assertIn(("REPORT_REMAINING_RECORD", "search_9"), [(s["operation"], s["resource_id"]) for s in plan.steps])

    def test_manifest_capture_time_stays_the_authority_for_the_screenshot_as_a_whole(self):
        ex = self.facts_for(good(displayed_times=[{"text": "04 Sep 2026 10:00 UTC", "meaning": "newest event"}]), "2026-09-01T06:00:00Z")
        self.assertIsNone(ex.facts[0].observed_at)                       # the row itself shows no later time
        self.assertTrue(ex.facts[0].available_at("2026-09-03T12:00:00Z"))
        self.assertFalse(ex.facts[0].available_at("2026-08-31T00:00:00Z"))   # captured after this earlier audit: inadmissible

    def test_expiry_and_expected_dates_do_not_count_as_capture_time(self):
        data = good(displayed_times=[{"text": "25 Jun 2026 06:42 UTC", "meaning": "export expires"}, {"text": "26 May 2026", "meaning": "delivery expected"},
                                     {"text": "01 Sep 2026 05:00 UTC", "meaning": "created"}])
        ex = self.facts_for(data, "2026-09-01T06:00:00Z")
        self.assertIsNone(ex.facts[0].observed_at)
        self.assertFalse(any("temporal_conflict" in w for w in ex.warnings))

    def test_small_rounding_gap_is_not_flagged(self):
        ex = self.facts_for(good(), "2026-09-01T06:00:00Z")            # displays 06:00:14, a time of day only
        self.assertFalse(any("temporal_conflict" in w for w in ex.warnings))
        self.assertIsNone(ex.facts[0].observed_at)

    def test_strict_ownership_ignores_a_screenshot_link_that_is_not_yet_admissible(self):
        ex = self.facts_for(good(displayed_times=[]), "2026-09-05T00:00:00Z")
        om = build_owner_map(ex.facts, [], resolver())
        self.assertEqual(om.owner_of("search_9"), "user_2")
        self.assertIsNone(om.owner_of("search_9", as_of="2026-09-03T12:00:00Z"))


class DisputeTests(VisionCase):
    def test_a_screenshot_showing_someone_else_disputes_a_catalog_link(self):
        from .support import link_activity
        act = link_activity("activity_9", "marketing_1", "bob@x.com", "user_2", "2026-09-01T07:00:00Z")
        data = good(identifiers={"resource_ids": ["marketing_1"], "emails": ["stranger@elsewhere.org"], "user_refs": [], "phones": [], "reference_codes": []},
                    rows=[row("marketing_1 | stranger@elsewhere.org", ["marketing_1"], ["stranger@elsewhere.org"])],
                    possible_links=[{"resource_id": "marketing_1", "subject": "stranger@elsewhere.org", "basis": "same_panel", "quote": "marketing_1 | stranger@elsewhere.org"}])
        m = self.image(captured="2026-09-01T06:00:00Z")
        ex = to_extraction(m, parse_interpretation(json.dumps(data), file_id="evidence_i", path=m.rel_path, captured_at=m.captured_at))
        om = build_owner_map(ex.facts, [act], resolver())
        self.assertEqual(om.owner_of("marketing_1"), "user_2")           # the catalog row still says so ...
        self.assertTrue(om.is_disputed("marketing_1"))                   # ... but the visible content contradicts it
        self.assertFalse(om.is_disputed("marketing_1", as_of="2026-08-01T00:00:00Z"))      # a dispute is only known from its capture time on
        plan = make_pipeline(ex.facts, [act]).run([deletion_case(audit_at="2026-09-03T12:00:00Z")])["c1"]
        self.assertNotIn("marketing_1", [s["resource_id"] for s in plan.steps])

    def test_a_disputed_account_is_not_confirmed(self):
        from ghost_audit.attribution import AMBIGUOUS, AccountAttributor, CONFIRMED
        from .support import link_activity, submit
        act = [link_activity("activity_9", "account_5", "bob@x.com", "user_2", "2026-09-01T07:00:00Z")]
        data = good(identifiers={"resource_ids": ["account_5"], "emails": ["stranger@elsewhere.org"], "user_refs": [], "phones": [], "reference_codes": []},
                    rows=[row("account_5 | stranger@elsewhere.org", ["account_5"], ["stranger@elsewhere.org"])],
                    possible_links=[{"resource_id": "account_5", "subject": "stranger@elsewhere.org", "basis": "same_row", "quote": "account_5 | stranger@elsewhere.org"}])
        m = self.image(captured="2026-09-01T06:00:00Z")
        facts = to_extraction(m, parse_interpretation(json.dumps(data), file_id="evidence_i", path=m.rel_path, captured_at=m.captured_at)).facts
        om = build_owner_map(facts, act, resolver())
        res = AccountAttributor(resolver(), om, act).attribute([deletion_case()])
        self.assertNotEqual(res["c1"].status, CONFIRMED)


class ResilienceTests(VisionCase):
    def test_timeouts_are_retried_with_exponential_backoff_then_succeed(self):
        m = self.image()
        it, client, sleeps = self.interpreter({self.key(m): [TimeoutError_("t"), TimeoutError_("t"), json.dumps(good())]})
        r = it.interpret(m)
        self.assertEqual(r.status, OK)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual([s for s in sleeps if s >= 2.0], [2.0, 4.0])
        self.assertEqual(it.usage.totals()["requests"], 3)

    def test_persistent_timeout_ends_as_a_structured_error(self):
        m = self.image()
        it, client, _ = self.interpreter({self.key(m): TimeoutError_("t")})
        r = it.interpret(m)
        self.assertEqual(r.status, ERROR)
        self.assertEqual(len(client.calls), 3)
        self.assertIn("TimeoutError_", r.problems[0])
        self.assertTrue(to_extraction(m, r).unsupported)

    def test_rate_limits_and_server_errors_are_retried_but_bad_requests_are_not(self):
        m = self.image()
        it, client, _ = self.interpreter({self.key(m): [RateLimitError("429"), TransientError("503"), json.dumps(good())]})
        self.assertEqual(it.interpret(m).status, OK)
        m2 = self.image("b.png", b"y", "evidence_j")
        it2, client2, _ = self.interpreter({self.key(m2): InvalidRequestError("400")})
        self.assertEqual(it2.interpret(m2).status, ERROR)
        self.assertEqual(len(client2.calls), 1)

    def test_a_rejected_request_stops_the_run_instead_of_repeating_for_every_image(self):
        a, b, c = (self.image(f"{n}.png", n.encode(), f"evidence_{n}") for n in "abc")
        it, client, _ = self.interpreter({self.key(a): InvalidRequestError("request rejected (HTTP 404)")})
        out = it.interpret_all([a, b, c])
        self.assertEqual([r.status for r in out.values()], [ERROR, ERROR, ERROR])
        self.assertEqual(len(client.calls), 1)                      # only the first image was ever sent
        self.assertIn("skipped after a fatal error", out["evidence_b"].problems[0])

    def test_requests_are_paced(self):
        a, b = self.image("a.png", b"1", "evidence_a"), self.image("b.png", b"2", "evidence_b")
        now = [100.0]
        sleeps = []
        cfg = VisionConfig(enabled=True, min_interval_s=6.0)
        client = FakeVisionClient({self.key(a): json.dumps(good()), self.key(b): json.dumps(good())})
        interp = ScreenshotInterpreter(client, cfg, sleep=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s)), clock=lambda: now[0])
        interp.interpret_all([a, b])
        self.assertEqual(sleeps, [6.0])

    def test_request_budget_is_a_hard_cap(self):
        a, b, c = (self.image(f"{n}.png", n.encode(), f"evidence_{n}") for n in "abc")
        cfg = VisionConfig(enabled=True, min_interval_s=0.0, max_requests=2, max_attempts=1)
        client = FakeVisionClient(default=json.dumps(good()))
        interp = ScreenshotInterpreter(client, cfg, sleep=lambda s: None)
        out = interp.interpret_all([a, b, c])
        self.assertEqual([r.status for r in out.values()], [OK, OK, ERROR])
        self.assertEqual(len(client.calls), 2)
        self.assertIn("BudgetExceeded", out["evidence_c"].problems[0])

    def test_fatal_auth_error_stops_the_run_without_leaking_the_key(self):
        secret = "AIza-SECRET-KEY-VALUE"
        a, b = self.image("a.png", b"1", "evidence_a"), self.image("b.png", b"2", "evidence_b")
        it, client, _ = self.interpreter({self.key(a): AuthError("authentication/permission error (HTTP 401)")})
        out = it.interpret_all([a, b])
        self.assertEqual([r.status for r in out.values()], [ERROR, ERROR])
        self.assertEqual(len(client.calls), 1)                      # the second image was never sent
        blob = json.dumps({k: v.to_dict() for k, v in out.items()}) + json.dumps(it.usage.entries)
        self.assertNotIn(secret, blob)

    def test_cache_prevents_a_second_request_and_is_keyed_by_model_and_prompt_version(self):
        m = self.image()
        cache = str(self.tmp / "cache")
        it, client, _ = self.interpreter({self.key(m): json.dumps(good())}, VisionConfig(enabled=True, min_interval_s=0.0, cache_dir=cache))
        it.interpret(m)
        it2 = ScreenshotInterpreter(FakeVisionClient(), VisionConfig(enabled=True, min_interval_s=0.0, cache_dir=cache), sleep=lambda s: None)
        r = it2.interpret(m)
        self.assertEqual((r.status, r.usage), (OK, {"cache_hit": True}))
        other_model = ScreenshotInterpreter(FakeVisionClient(), VisionConfig(enabled=True, min_interval_s=0.0, cache_dir=cache, model="other-model"), sleep=lambda s: None)
        self.assertEqual(other_model.interpret(m).status, ERROR)     # no cached response for a different model, and the fake has none

    def test_usage_is_tracked_per_request(self):
        m = self.image()
        it, _, _ = self.interpreter({self.key(m): ModelResponse(json.dumps(good()), 2200, 900)})
        it.interpret(m)
        self.assertEqual(it.usage.totals(), {"requests": 1, "successful": 1, "input_tokens": 2200, "output_tokens": 900, "cache_hits": 0})

    def test_http_status_classification(self):
        for code, cls in ((429, RateLimitError), (408, TimeoutError_), (504, TimeoutError_), (500, TransientError), (503, TransientError),
                          (401, AuthError), (403, AuthError), (400, InvalidRequestError), (404, InvalidRequestError)):
            self.assertIsInstance(classify_api_error(code), cls, code)


class DisabledBehaviourTests(VisionCase):
    def test_disabled_by_default_makes_no_request_and_needs_no_key(self):
        m = self.image()
        client = FakeVisionClient({self.key(m): json.dumps(good())})
        r = ScreenshotInterpreter(client, VisionConfig()).interpret(m)
        self.assertEqual(r.status, DISABLED)
        self.assertEqual(client.calls, [])
        self.assertTrue(to_extraction(m, r).unsupported)
        self.assertEqual(ScreenshotInterpreter(None, CFG).interpret(m).status, DISABLED)

    def test_config_from_env_is_off_unless_explicitly_enabled(self):
        self.assertFalse(VisionConfig.from_env({}).enabled)
        self.assertFalse(VisionConfig.from_env({ENABLE_ENV: "true"}).enabled)
        self.assertTrue(VisionConfig.from_env({ENABLE_ENV: "gemini"}).enabled)

    def test_the_real_client_needs_all_three_opt_ins_and_never_echoes_the_key(self):
        secret = "SECRETKEY-123-DO-NOT-LEAK"
        cases = [({}, False, 3), ({ENABLE_ENV: "gemini"}, False, 2), ({ENABLE_ENV: "gemini", KEY_ENV: secret}, False, 1), ({KEY_ENV: secret}, True, 1)]
        for env, approve, n_missing in cases:
            cfg = VisionConfig.from_env(env)
            with self.assertRaises(VisionDisabled) as cm:
                build_real_client(cfg, approve, env)
            msg = str(cm.exception)
            self.assertNotIn(secret, msg)
            self.assertEqual(msg.count(", ") + 1, n_missing, msg)
        self.assertIn("--approve-real-calls", str(self._refusal({ENABLE_ENV: "gemini", KEY_ENV: secret}, False)))
        self.assertIn(ENABLE_ENV, str(self._refusal({KEY_ENV: secret}, True)))

    @staticmethod
    def _refusal(env, approve):
        try:
            build_real_client(VisionConfig.from_env(env), approve, env)
        except VisionDisabled as exc:
            return exc
        raise AssertionError("expected VisionDisabled")

    def test_nothing_but_the_image_and_the_fixed_prompt_is_sent(self):
        m = self.image()
        it, client, _ = self.interpreter({self.key(m): json.dumps(good())})
        it.interpret(m)
        call = client.calls[0]
        self.assertEqual(set(call), {"image_sha256", "mime_type", "prompt_chars", "model"})
        self.assertEqual(call["prompt_chars"], len(PROMPT))
        self.assertNotRegex(PROMPT, r"[\w.]+@[\w.]+")
        self.assertNotIn("case_", PROMPT)
        self.assertNotIn("user_", PROMPT.replace("user_NN", ""))

    def test_vision_modules_do_not_import_the_sdk_at_import_time(self):
        code = "import sys, ghost_audit.vision.client, ghost_audit.vision.interpreter, ghost_audit.vision.facts; print('google.genai' in sys.modules)"
        out = subprocess.run([sys.executable, "-c", code], cwd=str(CODE_DIR), capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "False", out.stderr)


class TimeAndEstimateTests(unittest.TestCase):
    CAP = "2026-09-01T06:00:00Z"

    def test_display_time_formats(self):
        p = lambda s, cap=None: (lambda r: r.iso if r else None)(parse_display_time(s, cap or self.CAP))
        self.assertEqual(p("2026-09-01T05:58:21Z"), "2026-09-01T05:58:21Z")
        self.assertEqual(p("01 Sep 2026 06:00:14 UTC"), "2026-09-01T06:00:14Z")
        self.assertEqual(p("24 Sep 2026 09:14 JST"), "2026-09-24T00:14:00Z")                # JST is UTC+9
        self.assertEqual(p("20 Jun 14:18 UTC", "2026-06-18T12:00:00Z"), "2026-06-20T14:18:00Z")   # year taken from the capture
        self.assertEqual(p("28 Dec 10:00", "2026-01-05T00:00:00Z"), "2025-12-28T10:00:00Z")       # year rolls back
        self.assertEqual(p("08 Jul", "2026-07-13T10:00:00Z"), "2026-07-08T00:00:00Z")
        self.assertIsNone(p("06:46"))                                                         # time-only: no date, ignored
        self.assertIsNone(p("Ready for download"))
        self.assertTrue(parse_display_time("20 Jun 14:18", "2026-06-18T12:00:00Z").year_assumed)

    def test_latest_displayed_ignores_unparseable(self):
        self.assertEqual(latest_displayed(["06:46", "24 May 07:46", "24 May 08:15", "junk"], "2026-05-24T07:00:00Z"), "2026-05-24T08:15:00Z")
        self.assertIsNone(latest_displayed(["junk"], self.CAP))

    def test_image_token_formula_matches_the_documented_example(self):
        self.assertEqual(image_tokens(960, 540), 1548)          # 6 tiles x 258, as in the official example
        self.assertEqual(image_tokens(300, 300), 258)
        self.assertEqual(image_tokens(1800, 1100), 258 * 3 * 2)

    @needs_dataset
    def test_estimate_for_the_real_screenshots(self):
        imgs = [m.path for m in load_manifest() if m.suffix == ".png"]
        e = estimate(imgs, "gemini-2.5-flash", 3)
        self.assertEqual((e.images, e.requests_expected, e.requests_worst_case), (11, 11, 33))
        self.assertLess(e.cost_high, 0.2)
        self.assertGreater(e.cost_low, 0)
        self.assertIsNone(estimate(imgs, "no-such-model").cost_low)


@needs_dataset
class RealScreenshotFixtureTests(unittest.TestCase):
    """The hand-transcribed stand-in responses drive the fake client over the real PNGs (no network)."""

    @classmethod
    def setUpClass(cls):
        cls.manifest = load_manifest()
        cls.interp = ScreenshotInterpreter(fake_client_from_fixture(FIXTURE, cls.manifest), VisionConfig(enabled=True, min_interval_s=0.0), sleep=lambda s: None)
        cls.results = cls.interp.interpret_all(cls.manifest)

    def test_all_eleven_screenshots_are_interpreted(self):
        self.assertEqual(len(self.results), 11)
        self.assertTrue(all(r.status == OK for r in self.results.values()), {k: r.status for k, r in self.results.items()})

    def test_every_supported_link_is_visibly_grounded_and_every_fact_uses_the_manifest_file_id(self):
        for fid, r in self.results.items():
            for l in r.links:
                if l.supported:
                    self.assertIn(l.resource_id.lower(), l.quote.lower())
                    self.assertIn(l.subject.lower(), l.quote.lower())
            for f in to_extraction(next(m for m in self.manifest if m.file_id == fid), r).facts:
                self.assertEqual(f.file_id, fid)

    def test_comparison_changes_only_cases_with_directly_visible_new_evidence(self):
        from ghost_audit.vision.compare import compare
        res = compare(self.results)
        changed = {c["case_id"]: c for c in res["changes"]}
        self.assertEqual(set(changed), {"case_34", "case_35", "case_39", "case_45", "case_50", "case_52", "case_53", "case_63"})
        self.assertEqual(changed["case_34"]["status"], ("COMPLETE", "INCOMPLETE"))
        self.assertEqual(changed["case_52"]["status"], ("COMPLETE", "INCOMPLETE"))
        added = lambda cid: [tuple(x) for x in changed[cid]["added"]]
        self.assertIn(("FINDING", "REPORT_REMAINING_RECORD", "account_service", "account_5762", "evidence_04"), added("case_34"))
        self.assertIn(("FINDING", "REPORT_ACTIVE_DATA_FLOW", "integration_service", "data_flow_1121", "evidence_14"), added("case_39"))
        # case_52: the export row itself is dated 18 Jun (before the 19 Jun audit); the 20 Jun row on the page belongs to someone else
        self.assertIn(("FINDING", "REPORT_REMAINING_RECORD", "export_service", "export_1260", "evidence_23"), added("case_52"))
        # case_45: of the six audit rows only the one a catalog row names is admitted
        self.assertEqual([k for k in added("case_45") if k[3].startswith("audit_record")],
                         [("VERIFICATION", "VERIFY_RECORD_PRESENT", "security_service", "audit_record_003", "evidence_37")])
        # case_63: the console shows marketing_001 belonging to someone else, so the catalog link is disputed and nothing is asserted
        self.assertFalse(any(k[3] == "marketing_001" for k in changed["case_63"]["added"]))
        self.assertTrue(any(k[3] == "marketing_001" for k in changed["case_63"]["removed"]))
        # screens that show no resource ID establish nothing (no filename/alias guessing): cases 46 and 61 do not change
        self.assertFalse({"case_46", "case_61", "case_55", "case_33"} & set(changed))
        # the official samples are unaffected
        for k in ("status_accuracy", "step_precision", "step_recall", "step_f1", "evidence_accuracy", "sequence_exact_rate"):
            self.assertEqual(res["sample_score_off"][k], res["sample_score_on"][k], k)

    def test_the_marketing_screenshot_contradicts_its_catalog_link(self):
        from ghost_audit.context import build_context
        ctx = build_context(interpretations=self.results)
        self.assertTrue(ctx.owners.is_disputed("marketing_001"))

    def test_disabled_pipeline_equals_the_pipeline_without_interpretations(self):
        from ghost_audit.context import build_context
        a, b = build_context(), build_context(interpretations={})
        self.assertEqual([f.resource_id for f in a.facts], [f.resource_id for f in b.facts])


class CorroborationAndTranscriptionTests(VisionCase):
    ROWS = ((1, "20 Aug 09:00"), (2, "21 Aug 09:00"), (3, "22 Aug 09:00"))

    def audit_screen(self):
        rows = [row(f"{t} | event | user_2 | audit_record_{n}", [f"audit_record_{n}"], [], ["user_2"]) for n, t in self.ROWS]
        links = [{"resource_id": f"audit_record_{n}", "subject": "user_2", "basis": "same_row", "quote": f"{t} | event | user_2 | audit_record_{n}"}
                 for n, t in self.ROWS]
        return good(identifiers={"resource_ids": [f"audit_record_{n}" for n, _ in self.ROWS], "emails": [], "user_refs": ["user_2"], "phones": [],
                                 "reference_codes": []}, rows=rows, possible_links=links, displayed_times=[])

    def test_audit_event_rows_need_a_catalog_link_row_naming_the_resource(self):
        m = self.image()
        it = parse_interpretation(json.dumps(self.audit_screen()), file_id="evidence_i", path=m.rel_path, captured_at=m.captured_at)
        self.assertEqual({f.resource_id for f in to_extraction(m, it, corroborated={"audit_record_2"}).facts}, {"audit_record_2"})
        none = to_extraction(m, it)
        self.assertEqual(none.facts, [])
        self.assertTrue(any("need an explicit catalog link row" in w for w in none.warnings))

    def test_other_categories_do_not_need_corroboration(self):
        m = self.image()
        it = parse_interpretation(json.dumps(good()), file_id="evidence_i", path=m.rel_path, captured_at=m.captured_at)
        self.assertEqual([f.resource_id for f in to_extraction(m, it).facts], ["search_9"])

    def test_transcription_loader_validates_and_checks_the_image_hash(self):
        from ghost_audit.vision.transcribed import load_transcribed_interpretations
        m = self.image()
        digest = self.key(m)
        path = self.tmp / "t.json"
        entry = {"file": m.rel_path, "sha256": digest, "response": good()}
        path.write_text(json.dumps({"screenshots": {"evidence_i": entry}}), encoding="utf-8")
        ok = load_transcribed_interpretations([m], path)["evidence_i"]
        self.assertEqual((ok.status, ok.file_id, ok.captured_at), (OK, "evidence_i", m.captured_at))
        entry["sha256"] = "0" * 64                                   # a different image than the one transcribed
        path.write_text(json.dumps({"screenshots": {"evidence_i": entry}}), encoding="utf-8")
        bad = load_transcribed_interpretations([m], path)["evidence_i"]
        self.assertEqual(bad.status, ERROR)
        self.assertTrue(to_extraction(m, bad).unsupported)
        entry.update(sha256=digest, response={"readable": True, "identifiers": "broken"})   # an invalid transcription meets the same validator
        path.write_text(json.dumps({"screenshots": {"evidence_i": entry}}), encoding="utf-8")
        self.assertEqual(load_transcribed_interpretations([m], path)["evidence_i"].status, MALFORMED)
        self.assertEqual(load_transcribed_interpretations([m], self.tmp / "missing.json"), {})

    @needs_dataset
    def test_the_shipped_transcription_matches_the_real_screenshots(self):
        from ghost_audit.vision.transcribed import load_transcribed_interpretations
        got = load_transcribed_interpretations(load_manifest())
        self.assertEqual(len(got), 11)
        self.assertEqual({k: v.status for k, v in got.items()}, {k: OK for k in got})


if __name__ == "__main__":
    unittest.main()
