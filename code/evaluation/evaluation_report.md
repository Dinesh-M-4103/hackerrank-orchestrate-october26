# Evaluation report - Ghost Data Still Exists

Deterministic local pipeline; strict audit cutoff; no model calls in the submitted run. All numbers below are produced by `python code/evaluation/make_reports.py` from the code, not typed by hand.

## 1. Accuracy on the 32 official sample cases (measured)

The samples are the only ground truth available locally (`dataset/sample_output.csv`). They were never used to choose an answer: the pipeline reads only participant-facing evidence. They *were* used while developing rules, so these numbers are optimistic estimates, from only 32 cases, of accuracy on the 518 hidden answers.

| metric | value |
|---|---|
| case-level deletion-status accuracy | **96.9%** (31/32); confusion truth->pred: {'COMPLETE->COMPLETE': 9, 'COMPLETE->INCOMPLETE': 1, 'INCOMPLETE->INCOMPLETE': 22} |
| row accuracy (status and every step identical, in order) | **46.9%** (15/32) |
| step-level precision / recall / F1 (order-insensitive, all fields except step_no) | **100.0% / 85.5% / 92.2%** |
| step F1 by phase | FINDING 100.0%, REMEDIATION 100.0%, VERIFICATION 85.6% |
| field-level accuracy (positional, over expected steps) | step_no 85.5%, phase 85.5%, system_id 66.3%, resource_id 66.3%, operation 69.4%, state 69.4%, evidence_id 85.5%; overall 75.4% |
| evidence_id agreement on otherwise-matching steps | 100.0% |
| sequence accuracy: exact-sequence rate / mean LCS ratio | 46.9% / 87.6% |

Reading these honestly: precision is 100% because the pipeline only asserts steps it can ground in admissible evidence; recall is lower because it deliberately *omits* steps it cannot ground (see the mismatch causes below). A 100% precision on 32 cases does not imply 100% precision on the hidden set.

Naive baselines on the same samples (status only, no steps):

| baseline | status accuracy |
|---|---|
| all-incomplete | 68.8% |
| reported-status | 53.1% |

### Per-case differences (17 of 32 sample cases differ in any way; the other 15 are exact)

| case | truth -> predicted | missing | extra | cause of the missing steps |
|---|---|---|---|---|
| case_03 | COMPLETE -> COMPLETE | 1 | 0 | 1x anonymized record carries no identifier; ownership not derivable from admissible evidence |
| case_04 | COMPLETE -> INCOMPLETE **(status differs)** | 2 | 0 | 1x account not attributed (NONE/no_candidate); 1x anonymized record carries no identifier; ownership not derivable from admissible evidence |
| case_09 | INCOMPLETE -> INCOMPLETE | 2 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous); 1x retained audit record not discoverable from admissible evidence |
| case_18 | INCOMPLETE -> INCOMPLETE | 2 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous); 1x retained audit record not discoverable from admissible evidence |
| case_20 | INCOMPLETE -> INCOMPLETE | 2 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous); 1x retained audit record not discoverable from admissible evidence |
| case_21 | COMPLETE -> COMPLETE | 2 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous); 1x anonymized record carries no identifier; ownership not derivable from admissible evidence |
| case_22 | INCOMPLETE -> INCOMPLETE | 1 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous) |
| case_23 | INCOMPLETE -> INCOMPLETE | 2 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous); 1x retained audit record not discoverable from admissible evidence |
| case_24 | INCOMPLETE -> INCOMPLETE | 1 | 0 | 1x retained audit record not discoverable from admissible evidence |
| case_25 | COMPLETE -> COMPLETE | 2 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous); 1x anonymized record carries no identifier; ownership not derivable from admissible evidence |
| case_26 | COMPLETE -> COMPLETE | 2 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous); 1x anonymized record carries no identifier; ownership not derivable from admissible evidence |
| case_27 | COMPLETE -> COMPLETE | 2 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous); 1x anonymized record carries no identifier; ownership not derivable from admissible evidence |
| case_28 | COMPLETE -> COMPLETE | 2 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous); 1x anonymized record carries no identifier; ownership not derivable from admissible evidence |
| case_29 | INCOMPLETE -> INCOMPLETE | 2 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous); 1x retained audit record not discoverable from admissible evidence |
| case_30 | INCOMPLETE -> INCOMPLETE | 1 | 0 | 1x retained audit record not discoverable from admissible evidence |
| case_31 | COMPLETE -> COMPLETE | 1 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous) |
| case_32 | INCOMPLETE -> INCOMPLETE | 1 | 0 | 1x account not attributed (AMBIGUOUS/timing_ambiguous) |

Total extra (wrongly asserted) steps across the samples: 0. Status errors: 1 (case_04).

## 2. Additional evaluation cases written by the participant

7 synthetic cases (`code/evaluation/custom_cases.py`, run with `python code/evaluation/main.py --custom`). Expected outputs were written from the specification before running the pipeline. They are behavior checks authored by the same person who wrote the code, not independent validation.

| # | case | what it checks | result |
|---|---|---|---|
| 1 | replica survivor, active data flow and restorable backup | A record deleted from the primary but still on the failover replica is a surviving record; an enabled connector and a restorable archive are findings; the archive is retained (restore blocked, not deleted); the retained audit record is verified present; remediation follows the operation order DISABLE -> BLOCK -> DELETE. | **PASS** |
| 2 | evidence captured after audit_at must not change the answer | The search entry was absent at audit time and reappeared in a later capture. The later file is inadmissible; the case is COMPLETE and cites only the earlier file. The workflow's NOT_STARTED claim must not matter. | **PASS** |
| 3 | historical e-mail and an exposed account tombstone | The request uses the person's OLD e-mail. The tombstone must keep only the stable reference, so its visible name is a finding to REDACT (not delete); the absent account and the retained audit record are verified. | **PASS** |
| 4 | marketing opt-out is not a deletion request | Live resources exist and the workflow says COMPLETE, but the person explicitly does not ask for deletion: NOT_APPLICABLE with an empty steps array. | **PASS** |
| 5 | two people, two candidate accounts: do not guess, keep processing what is confirmed | Both requests could own either account (same reported_at, two candidate deletions). No account step may be asserted for either, but each person's independently confirmed resources are still processed. | **PASS** |
| 6 | a SUCCESS workflow claim is contradicted by the evidence | The deletion worker reports success and the request is marked COMPLETE, but the snapshot still holds the search entry. | **PASS** |
| 7 | active legal hold is retained; an expired retention period gets no invented treatment | The legal-hold record must remain (verify present). The audit record's retention ended before audit_at, and the specification does not say what applies then, so no step is generated for it. | **PASS** |

## 3. The final full run (518 requests) - validation and diagnostics

`python code/main.py run` wrote `output.csv` with the deterministic pipeline: strict audit cutoff, no model calls, no network. It also uses a committed, hash-checked *transcription* of the 11 PNG screenshots (see section 3a). Re-running it reproduces every row (regenerated rows identical to the file: **True**).

There is **no ground truth** for these 518 cases, so no accuracy is claimed for them. The checks below verify contract compliance and provenance, not correctness.

| check | result | detail |
|---|---|---|
| header is exactly case_id,deletion_status,steps_json | PASS | ['case_id', 'deletion_status', 'steps_json'] |
| row count equals deletion_requests.csv | PASS | 518 rows, 518 requests |
| every request has exactly one row, no extras | PASS | missing=[] extra=[] duplicates=0 |
| rows sorted by case_id | PASS | string order: True; numeric order: False (the specification does not say which; either is accepted unless --case-order is given) |
| deletion_status values are allowed | PASS | {'INCOMPLETE': 354, 'COMPLETE': 154, 'NOT_APPLICABLE': 10} |
| steps_json valid; seven fields; consecutive step_no; phase/operation/state pairs; ordering; evidence rules | PASS | no problems |
| NOT_APPLICABLE rows have steps_json == [] | PASS | [] |
| NOT_APPLICABLE rows are exactly the requests whose text does not authorize deletion | PASS | 10 not-applicable rows; text classifier says 10 |
| every cited evidence_id is a real file_id / activity_id / audit_id | PASS | 1039 citations; unknown=[] |
| no cited evidence was captured/logged after the case's audit_at | PASS | violations=[] |
| screenshot citations (allowed: admissible by manifest capture time, checked above) | PASS | 7 steps cite a screenshot file in 7 cases |
| INCOMPLETE rows that contain no finding (completeness could not be proven) | PASS | 3 rows |

- statuses: {'INCOMPLETE': 354, 'COMPLETE': 154, 'NOT_APPLICABLE': 10}; total steps: 2726 ({'FINDING': 713, 'REMEDIATION': 713, 'VERIFICATION': 1300}); evidence citations: 1039.
- account attribution over the 508 deletion requests: {'NONE': 10, 'CONFIRMED': 166, 'AMBIGUOUS': 274, 'PROBABLE': 58}. Rows with an AMBIGUOUS or unattributed account therefore contain **no account step**; 85 of the 154 COMPLETE rows are in that group.
- INCOMPLETE rows with no finding (completeness could not be proven): case_55, case_61, case_63.

### 3a. The 12 screenshot-dependent cases, re-checked against the screenshots

Eleven PNG console screenshots could not be read by a runtime model: the one real Gemini attempt was rejected by the API (HTTP 404, no result, 0 tokens). Instead they were **transcribed by the coding agent (Claude Code, claude-sonnet-5-5) by eye during development**, twice, into `code/evaluation/screenshot_transcriptions.json`. That file carries the SHA-256 of each PNG (the loader refuses a mismatch) and is run through the same validator a model response would meet. This is an *extraction by a model, done once, offline and committed*; the pipeline itself is deterministic given the file, but a transcription can contain reading mistakes. Cases whose output differs from a run that ignores screenshots: **8** (case_34, case_35, case_39, case_45, case_50, case_52, case_53, case_63); no case outside the 12 changes.

| case | audit_at | status without -> with screenshots | steps without -> with | what the screenshot directly shows |
|---|---|---|---|---|
| case_33 | 2026-07-19T13:00:00Z | COMPLETE -> COMPLETE | 1 -> 1 | evidence_23 shows export_1260 (owner ingrid.dahl@outlook.com) on 18 Jun; a later inventory (evidence_81, 3 Jul) no longer lists it, so it is verified absent. No change from the screenshot. |
| case_34 | 2026-09-27T13:00:00Z | COMPLETE -> INCOMPLETE | 2 -> 4 | evidence_04 (captured 26 Sep, before the 27 Sep audit) shows account_5762 for haruka.s1838@zoho.com / user_11 as a live profile, after earlier evidence_01 (15 Sep) had reported it not found: the account is back. |
| case_35 | 2026-09-02T08:00:00Z | COMPLETE -> INCOMPLETE | 1 -> 3 | evidence_09 (1 Sep, before the 2 Sep audit) shows search_428 for g.silva76@gmail.com / user_13 indexed 05:58 UTC, after the 27 Aug replica no longer listed it. The look-alike search_1874 (g.silva76.jr@...) is a different address and is not linked. |
| case_39 | 2026-08-08T10:00:00Z | INCOMPLETE -> INCOMPLETE | 1 -> 3 | evidence_14 (7 Aug) shows data_flow_1121 (selector jain.aditi@gmail.com) Healthy, continuous sync, events delivered at 07:59: an active flow. |
| case_45 | 2026-09-06T09:00:00Z | COMPLETE -> COMPLETE | 1 -> 1 | evidence_37 lists six audit events for user_03; only audit_record_003 is also named by a catalog link row, so only it is admitted. The step is unchanged but cites the later screenshot (evidence_37, 07:00) instead of the PDF extract (evidence_55, 06:30). |
| case_46 | 2026-03-11T08:00:00Z | COMPLETE -> COMPLETE | 1 -> 1 | evidence_42 shows the retained reference user_17 with no personal fields, but the resource ID tombstone_17 is not visible on the page. No link or state is inferred. |
| case_50 | 2026-07-14T12:00:00Z | INCOMPLETE -> INCOMPLETE | 1 -> 3 | evidence_19 (13 Jul) shows analytics_7438 with the name, user_20 and darwish.omar@icloud.com visible: still attributable, so anonymize. |
| case_52 | 2026-06-19T14:00:00Z | COMPLETE -> INCOMPLETE | 1 -> 3 | evidence_23 is catalogued 18 Jun 12:00 (before the 19 Jun audit) and its export_1260 row is dated 18 Jun 06:42; another customer's row dated 20 Jun is ignored. It shows the export Ready, although the database inventory evidence_78 (18 Jun 11:44) did not list it: the later source wins. |
| case_53 | 2026-05-25T09:00:00Z | INCOMPLETE -> INCOMPLETE | 0 -> 3 | evidence_28 (24 May) shows the requester's name and kagyeman8947@fastmail.com on support_1034: identifiers present in a support record, so redact. |
| case_55 | 2026-04-30T11:00:00Z | INCOMPLETE -> INCOMPLETE | 1 -> 1 | evidence_32 shows backup_9287 (areed1433@gmail.com) as an Archive but shows no restore-permission field: restore access is unknown, so only a planned final-state check remains. No change. |
| case_61 | 2026-02-14T10:00:00Z | INCOMPLETE -> INCOMPLETE | 1 -> 1 | evidence_46 lists delivery_confirmation_19.pdf but no resource ID or owner; the file name alone is not accepted as a link. No change. |
| case_63 | 2026-09-17T09:20:00Z | INCOMPLETE -> INCOMPLETE | 1 -> 0 | evidence_60 shows marketing_001 belonging to Soo-jin Park (sj.park@naver.com), contradicting the catalog row that ties it to kwame-mensah@proton.me: the resource is disputed and not asserted for either person. |

## 4. Failures found and improvements made

Measured on the 32 samples while building (first full planner run -> final): status accuracy 81.2% -> 96.9%, step recall 74.1% -> 85.5%,
precision 97.9% -> 100%. The defects below were found by diffing the pipeline against the samples *and* by writing probe cases, and each is
covered by a unit test.

| # | defect found | consequence | fix |
|---|---|---|---|
| 1 | JSON bundle snapshots were treated as full enumerations | retained audit records and accounts of other people were declared "gone" because a bundle did not list them | bundles are subject-scoped: silence proves absence only for subjects present in that file |
| 2 | a later snapshot of one database node superseded presence on a different node | a record still on the failover replica was reported as deleted (case_05) | supersession needs a later snapshot of the same node role |
| 3 | contact-index lookups classified as support messages | a legacy contact record was skipped instead of deleted (cases 10, 12) | `contact_*_completed` events map to SECONDARY_IDENTIFIER (rule_02) |
| 4 | retained categories (audit, legal hold, backup, tombstone) declared absent by snapshot silence | missing retained-record steps | only an explicit not-found counts for those categories |
| 5 | one person with two linked accounts | the older account of an earlier deletion run was pulled into case_17 | timing disambiguates between linked accounts; never overrides a single confirmed one |
| 6 | intent classifier: refusals were too broad ("do not delete my invoices", "I still want my account deleted"), conditional refusals, hedges, an Italian stem | wrong NOT_APPLICABLE or wrong deletion | negations must target the account itself; hedge and conditional rules; probe tests with unseen multilingual texts |
| 7 | data flow / backup rows with no enabled/restore field read as compliant | silent false "disabled/blocked" | such rows are "unknown" and receive only a planned final-state check |
| 8 | a rejected API request (404/400) would have been repeated for every image | wasted requests | a rejected request is fatal for the run (screenshot feature only) |
| 9 | evidence captured after audit_at | would change answers silently | whole-file inadmissibility, a hard checker over every cited ID, tests |
| 10 | a first screenshot rule discarded a whole screenshot because one row on the page displayed a later date | case_52's own export row (dated before its audit) was thrown away | manifest capture time is the authority for the screenshot; admissibility is then checked per row from the row's own displayed time; the page-level mismatch is only flagged |
| 11 | an audit-event screen lists six routine events for one person | six retained-record steps instead of one | rows of such screens are admitted only when a catalog link row also names the resource |

## 5. Known limitations and unverified claims

* **Accuracy outside the samples is unknown.** 32 samples, tuned against; no ground truth for the 518.
* **Ambiguous accounts are omitted, not guessed.** When several cases share a reported_at and several accounts could belong to them, no
  account step is produced (see the attribution counts above). This is the largest source of missing steps; an exact hidden answer that
  contains the account step will therefore not match exactly. Candidates always share a state (all absent or unobserved), so the
  *status* is not at risk from this (measured with `python code/main.py ambiguity-report`).
* **Anonymized records with no identifier cannot be attributed** to anyone from admissible evidence, and some retained audit records are
  only visible in evidence captured after the audit (or nowhere), so their verification steps are missing.
* **Screenshot evidence rests on an agent transcription, not a runtime model.** The real Gemini attempt failed (HTTP 404, model not available to the
  key). The 11 screenshots were read by eye by the coding agent and committed with image hashes (section 3a). A misread would flow into the
  answers; the transcription was checked by reading each image twice, but it has no independent verification. Screens that show no resource ID
  (evidence_42, evidence_46) or no restore field (evidence_32) establish nothing, and the contradictory marketing screenshot (evidence_60) is
  treated as a dispute, so several of the 12 cases remain low-confidence.
* **Several screenshots display times later than their manifest capture time** (evidence_19, 23, 28, 42). The manifest time decides whether a
  screenshot may be used; a row that itself shows a later time is excluded for earlier audits. This is a judgement about an inconsistency in the
  data, not something the specification settles.
* **"Not listed" is weak evidence of absence.** In case_52 a database inventory did not list an export that a screenshot taken 16 minutes later
  shows Ready; the later source wins, but a single snapshot's silence can mislead whenever a record lives in a layer the snapshot does not cover.
* **Cases with nothing assessable** are reported INCOMPLETE with no findings, because completeness cannot be proven; a COMPLETE answer would
  require citable verification evidence. The count is in the validation table above.
* **Specification gaps handled conservatively, with no invented policy:** a retained record that is missing, a REDACT-category record that was
  deleted, an expired retention or legal-hold period, support messages whose PII status is unknowable, and whether support conversations
  always contain shared context.
* **Row order is ambiguous in the specification.** "Sorted by `case_id`" does not say string or numeric order. Every organizer-produced file
  (`deletion_requests.csv`, `user_audit_log.csv`, `user_identities.csv`, `system_activity.csv`) is in numeric order, never in string order, and IDs are
  zero-padded to two digits and unpadded beyond (`case_01` ... `case_550`), so the samples (`case_01`..`case_32`) are in both orders at once.
  `python code/main.py run --case-order numeric|string` writes either; the checker accepts either unless told otherwise. See README.
* **Heuristics calibrated on the data:** the 30-day staleness horizon and the 30-90 minute deletion window were set from observed evidence
  (documented in `README.md` and `ghost_audit/attribution.py`); they are not part of the specification.
* The 10 non-deletion request texts were read before the intent patterns were written, so the 10/10 result on them is not out-of-sample.
  Out-of-sample behavior is covered only by the unit-test probes.

## 6. Reproduce

```
python -m venv .venv && .venv\Scripts\activate && python -m pip install -r requirements.txt
python -m unittest discover -s code          # all offline tests
python code/main.py run                      # writes output.csv at the repository root and validates it
python code/main.py check-output output.csv  # independent validation
python code/evaluation/main.py --custom      # participant-written evaluation cases
python code/evaluation/make_reports.py       # regenerate these reports from live measurements
```
