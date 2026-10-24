# Screenshot evidence inspection (all 11 PNGs)

Method: each image was opened and read by eye with the local image viewer (no OCR, no model call), **twice**, and compared with the
transcription. Text below is **directly visible** unless marked *inferred*. The transcription is committed as
`code/evaluation/screenshot_transcriptions.json` (with the SHA-256 of each PNG) and is what `python code/main.py run` uses for screenshot
evidence; it passes through the same validator as an optional Gemini response. No golden labels or sample answers were consulted. The one real
Gemini request failed (HTTP 404) and contributed nothing.

"Pairing" = does the visible content support the activity `LINK_RESOURCE_IDENTITY` row that mentions the same resource?
File names and manifest proximity were **not** treated as establishing identity.

| ID | File (manifest capture) | Application / screen | Directly visible identifiers | Displayed times | Pairing with the catalog link row | Uncertainty |
|---|---|---|---|---|---|---|
| evidence_04 | `account_admin_console_20260926.png` (2026-09-26T11:00Z) | Customer Operations, customer overview | Haruka Sato; `user_11`; `account_5762`; `haruka.s1838@zoho.com`; `+81 90 7610 2507`; `CS-8538` / `support_9538`; orders `ORD-20265762`, `ORD-20265518` | last contact 24 Sep 2026 09:14 JST; 28 Aug support created | **Consistent** with activity_4000 (account_5762 ~ haruka.s1838@zoho.com, user_11): resource, user and e-mail share one panel. `support_9538` sits in a details panel with no subject: link is only a *hypothesis*. | No capture time shown. Earlier log evidence_01 (09-15) reported account_5762 not found; this screenshot shows the profile present on 09-26 (recreation, *inferred* from two admissible observations). |
| evidence_09 | `search_index_inspector_20260901.png` (2026-09-01T06:00Z) | Search Console, Customer Search | `search_428` Gabriel Silva `g.silva76@gmail.com` `user_13`; decoy `search_1874` `g.silva76.jr@gmail.com` (no reference); recent queries mention other people (`user_9842`, "Aditi Jain") | Executed 01 Sep 2026 06:00:14 UTC; indexed_at 2026-09-01T05:58:21Z | **Consistent** with activity_4001 (search_428 ~ g.silva76@gmail.com, user_13). The look-alike `…76.jr@…` is a different token and is not linked to the person. | 14 s later than the rounded manifest time (below the 5-minute tolerance). |
| evidence_14 | `connector_management_console_20260807.png` (2026-08-07T08:00Z) | BridgeOps, Connections | `data_flow_1121` selector `jain.aditi@gmail.com`; `data_flow_9846` selector `lucia-torres@gmail.com`; both "Continuous", status Healthy | header "07 Aug 2026 08:00 UTC"; last contact 07:59:42 | **Consistent** with activity_4002 (data_flow_1121 ~ jain.aditi@gmail.com, user_14). | "Continuous / Healthy" is read as *enabled* by a fixed vocabulary (inference); no explicit enabled flag is shown. |
| evidence_19 | `analytics_profile_view_20260713.png` (2026-07-13T10:00Z) | Pulse Analytics, user profile | Omar Darwish; `user_20 · analytics_7438 · darwish.omar@icloud.com` | last activity 13 Jul 2026 14:22 UTC; first seen 21 Apr 2026 | **Consistent** with activity_4003. | Displays 14:22, **4 h 22 min after** the manifest capture time 10:00 (temporal conflict, flagged). |
| evidence_23 | `customer_export_console_20260618.png` (2026-06-18T12:00Z) | Customer Download Center, Data exports | `export_1260` `ingrid.dahl@outlook.com` Ready; also `export_1261` (marcus.lee@northstar.shop), `1259` (sara.kim@…), `1258` (daniel.park@…) | export_1260 created 18 Jun 06:42 UTC; **export_1261 created 20 Jun 14:18 UTC**; expires 25 Jun | **Consistent** with activity_4004 (export_1260 ~ ingrid.dahl@outlook.com, user_18). | Shows a row dated 20 Jun although catalogued as captured 18 Jun. For the case audited 19 Jun (case_52) the content postdates the audit, so the image is treated as inadmissible for it; for the 19 Jul audit (case_33) it is admissible. |
| evidence_28 | `support_ticket_console_20260524.png` (2026-05-24T07:00Z) | Helpline Console, ticket `CS-5034` | `support_1034` Kofi Agyeman; `kagyeman8947@fastmail.com`; order `ORD-20265034` | activity 24 May 07:46 and 08:15; delivery expected 26 May | **Consistent** with activity_4005 (support_1034 ~ kagyeman8947@fastmail.com, user_22). | Activity at 08:15 is after the 07:00 manifest time (flagged). The requester name/e-mail are on the page: *inferred* as "requester identifiers present". |
| evidence_32 | `backup_restore_console_20260429.png` (2026-04-29T09:00Z) | Vault Recovery Manager, archives | `backup_9287` `areed1433@gmail.com` eu-west-1; three other accounts (company/other addresses) | created 26 Apr 02:00 UTC; last drill 29 Mar; retention end 26 Jul | **Consistent** with activity_4006 (backup_9287 ~ areed1433@gmail.com, user_16). | **No restore-allowed field is visible** (only tier "Archive", region, encryption, checksum). Restore access is therefore *unknown* from this image; log evidence_01 (09-15) says restorable but is after this case's audit. |
| evidence_37 | `security_audit_explorer_20260905.png` (2026-09-05T07:00Z) | Sentinel, Account events (dark theme) | Subject filter `user_03`; six events `audit_record_001…006`, all subject `user_03`; `audit_record_003` = "Account closure recorded", recorded_at 2026-08-25T04:03:00Z | 18–26 Aug 2026 filter; event times 18–25 Aug | **Consistent** with activity_4010 (audit_record_003 ~ oliver-bennett@gmail.com, user_03). | **Six** rows are visibly subject `user_03`, the catalog row names only one. Rule applied: every visible row linked to a resolvable subject is the person's. Whether the other five count as case resources is an open question (see report). |
| evidence_42 | `deleted_account_reference_20260310.png` (2026-03-10T06:00Z) | Customer Reference Registry | `user_17` "Retained reference"; linked `ORD-20263245`, `CS-3245`, `CS-3378`, `FIN-7742`; no name/e-mail/phone | most recent update 10 Mar 2026 07:18 UTC | **Partly consistent** with activity_4011 (tombstone_17 ~ leonie.g9352@outlook.com, user_17): the subject `user_17` matches, but **`tombstone_17` is not visible** on the page. | The resource ID is not shown, so no fact is created; update time is after the manifest capture time (flagged). Stale (>30 days) for the 15 Sep sample audit. |
| evidence_46 | `object_storage_browser_20260213.png` (2026-02-13T08:00Z) | Nimbus Object Storage, bucket listing | file names only (`delivery_confirmation_19.pdf`, `…_20.pdf`, return labels, exports); uploader `support-service` | "Captured 13 Feb 2026 08:00 UTC" (matches manifest) | **Not verifiable.** activity_4007 says object_19 (delivery_confirmation_19.pdf) belongs only to priya.raman@gmail.com / user_07; the image shows the file name and no owner or resource ID. | Filename similarity does **not** establish ownership; no fact is created. |
| evidence_60 | `marketing_profile_console.png` (2026-09-16T07:20Z) | Audience Operations, marketing profile | `marketing_001 · sj.park@naver.com · +82 10-5837-9451`, Soo-jin Park, email opt-in Yes, "Live" | last refreshed 15 Sep 2026 23:40 UTC | **CONTRADICTS** activity_4008, which links `marketing_001` to kwame-mensah@proton.me / user_09. `sj.park@naver.com` is not in the identity file or any other evidence. | Treated as a *dispute*: marketing_001 is not asserted for either person. |

## Cross-checks against existing facts (admissible-evidence view)

| Resource | Other evidence | Relation to the screenshot |
|---|---|---|
| account_5762 | evidence_01 (09-15): lookup not found | screenshot (09-26) shows it present → state changed / recreated before the 09-27 audit of case_34 |
| search_428 | evidence_10 (08-27 replica) does not list it | screenshot (09-01) shows it indexed → present before the 09-02 audit of case_35 |
| data_flow_1121, analytics_7438, export_1260, backup_9287, support_1034 | only log/bundle evidence captured **after** the corresponding audits (inadmissible) | the screenshots are the only admissible state evidence for those cases |
| audit_record_003 | evidence_55 (PDF, 09-05T06:30): present | consistent with the screenshot |
| marketing_001, tombstone_17, object_19 | none | cannot be corroborated |

## Open pairing issues
1. evidence_60 contradicts its catalog link (dispute handling above).
2. evidence_42 and evidence_46 are paired with their catalog rows only by subject / file name, not by a visible resource ID.
3. evidence_37 shows five more audit records than the catalog row names.
4. Four screenshots (19, 23, 28, 42) display times later than their manifest capture times; only evidence_23 crosses an audit cutoff (case_52).

## Final admissibility and linking rules used for screenshots

1. The **manifest capture time** decides whether a screenshot may be used for a case (it must be at or before `audit_at`).
2. A **row** that itself displays a date/time after its capture time is valid only from that time on, so it is excluded for earlier audits.
   Displayed times that describe the future (expiry, expected delivery, retention end) are ignored. A page that displays times later than
   its manifest time is flagged but its other rows are kept (evidence_19, 23, 28 and 42 show such times).
3. A resource is taken from a screenshot only when its **resource ID is visible**; a subject is attached only through a same-row/same-panel link whose
   quote contains both. File names, titles and proximity never link anything (evidence_42, evidence_46 therefore establish nothing).
4. Audit-event screens contribute only rows that an explicit catalog link row also names (evidence_37: audit_record_003 only).
5. A screenshot that shows a different, unresolvable subject for a catalog-linked resource **disputes** the link (evidence_60 / marketing_001).
6. States are never taken from model/transcription output: they come from fixed rules over visible fields (identifiers shown -> still attributable;
   "Continuous / Healthy" with events delivered -> active flow; a backup row with no restore field -> restore access unknown).

## Recheck of the 12 screenshot-dependent cases (screenshots ignored vs used)

Produced by `python code/evaluation/make_reports.py`; the same table with the measured step counts is section 3a of `evaluation_report.md`.

| case | audit_at | result with screenshots | basis (directly visible, admissible) |
|---|---|---|---|
| case_33 | 2026-07-19 | unchanged (COMPLETE) | evidence_23 shows export_1260 on 18 Jun; a later inventory (3 Jul) no longer lists it |
| case_34 | 2026-09-27 | COMPLETE -> **INCOMPLETE** | evidence_04 (26 Sep): account_5762 is a live profile for haruka.s1838@zoho.com / user_11, after evidence_01 (15 Sep) had it not found |
| case_35 | 2026-09-02 | COMPLETE -> **INCOMPLETE** | evidence_09 (1 Sep): search_428 for g.silva76@gmail.com / user_13 is indexed; look-alike search_1874 is not linked |
| case_39 | 2026-08-08 | INCOMPLETE (finding added) | evidence_14 (7 Aug): data_flow_1121 for jain.aditi@gmail.com Healthy, continuous sync |
| case_45 | 2026-09-06 | unchanged status; citation now evidence_37 | only audit_record_003 is admitted (catalog link row); later than evidence_55 (PDF) |
| case_46 | 2026-03-11 | unchanged | evidence_42 shows reference user_17 but no resource ID |
| case_50 | 2026-07-14 | INCOMPLETE (finding added) | evidence_19 (13 Jul): analytics_7438 shows name, user_20 and e-mail |
| case_52 | 2026-06-19 | COMPLETE -> **INCOMPLETE** | evidence_23 (18 Jun 12:00): export_1260 (ingrid.dahl@outlook.com) Ready, created 18 Jun 06:42; the 20 Jun row is another customer's. Conflicts with database inventory evidence_78 (18 Jun 11:44), which does not list it; the later source wins |
| case_53 | 2026-05-25 | INCOMPLETE (finding added) | evidence_28 (24 May): support_1034 shows requester name and kagyeman8947@fastmail.com |
| case_55 | 2026-04-30 | unchanged | evidence_32 shows backup_9287 but no restore-permission field |
| case_61 | 2026-02-14 | unchanged | evidence_46 lists a file name only; no resource ID or owner |
| case_63 | 2026-09-17 | INCOMPLETE (planned check removed) | evidence_60 shows marketing_001 for someone else: disputed, not asserted |

Cases 34, 35 and 52 flip from COMPLETE to INCOMPLETE; they rest on one screenshot each and are the most consequential reading of an image.
