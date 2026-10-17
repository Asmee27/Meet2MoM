# Golden fixture: demo_01

**What it is:** a regression snapshot of pipeline outputs, **not ground truth**.

| Item | Value |
|---|---|
| Source recording | `input/test_meeting.mp4` (248.13 s, English, 5 diarized speakers) |
| Source folder | `storage/demo_01/` (left in place, unmodified) |
| Produced | 2026-09-10 (`meetings.created_at` 2026-09-10T16:44:48, filename `demo_01.wav`) |
| Copied | 2026-10-16, byte-identical (verified with `cmp`) |
| Claims mode | `rules` (`claims.json` stats `mode_used: rules`) |

## Files and SHA-256

| File | Origin in `storage/demo_01/` | SHA-256 |
|---|---|---|
| `meeting_transcript_ready.json` | `transcript/` | `1402658dc840faeb2df201c3888c3fe436350140d1027190650940a3bb742cfa` |
| `transcript_index.json` | `transcript/` | `61f3617435f813ba8c998ccce7c3eb2ee9416244cf14ebbbac01db03d8bc9ea6` |
| `frontend_transcript.json` | `transcript/` | `8e02477f9a7bd324e150c4fb3ff74b55aae75d8df87ff009bad74a2767ccdcf9` |
| `topics.json` | `intelligence/` | `d26a889b7f89882b2dc0db0e154c81e4ba7cbe792d7df5438528028e6542371b` |
| `decisions.json` | `intelligence/` | `e6f23289212da67b974b620af9e89e6d1bf604d0b2620fe9f55645628a922b42` |
| `action_items.json` | `intelligence/` | `50be183ba9ec2929262739b9faee83f4b7102607e8fafc7b7118d9158e70455b` |
| `claims.json` | `intelligence/` | `5d90ffdf49e41decb4ea3a81597605bb2c557e0e8e58008da04d06edd229a62a` |
| `verified_intelligence.json` | `verification/` | `446c029ef0322080d2ba7d5b64298b9bc7d99cb660bac4b37ad7d1f27e3bf68c` |

## Expected outputs (each re-derived from the inputs above by `tests/test_golden_demo_01.py`)

| Stage | Expected output |
|---|---|
| Transcript index | 39 utterances `u00000`–`u00038`, 5 speakers |
| Rule decisions | 2: "Start with a review of the current training approach and look at what we need to do to address the problems brought up in the survey." and "Look at the report together." |
| Rule action items | 0 |
| EvidenceVerifier | decision 1 `verified_with_warning` (SPEAKER_01, 201.33 s); decision 2 `review` (SPEAKER_01, 217.81 s) |
| Claims (rules mode, DB) | 2 decisions, evidence `u00030` and `u00033`, 0 dropped |

The stored `mom.json` (not frozen, since it embeds the template summary) lists 1 confirmed decision, because decision 2 is `review` and is excluded.

## Known defects pinned by this snapshot

- Decision 1 cites `u00030`; the roadmap flags this as a likely mis-citation.
- The recording contains commitments the rule extractor misses (0 action items).
- Topic labels are noisy ("Refresher Course / Aren").

If a later phase improves these, update the fixture **deliberately** and record why.

## Change log

- **Phase 1 (2026-10-16), rules-v2:** rules-mode claims changed on purpose and are now pinned in `test_golden_demo_01.py::RULES_V2_CLAIMS`:
  - Decision 1 now cites `u00030`–`u00032`, the full proposal plus "Yes."; v1 cited only `u00030`, chosen by an interpolated timestamp.
  - Decision 2 now cites `u00034`–`u00035`; v1 cited `u00033`, which does not contain the decision.
  - Two unconfirmed proposals are kept as `proposed` claims instead of being dropped.

  `claims.json` here is **unchanged** (v1, byte-identical). It's the input for the Phase 1 Gate C check that the semantic verifier flags the `u00030` citation.

## ami_es2002a

Requested as a second golden fixture, but **it doesn't exist on this machine**: there is no `storage/ami_es2002a/` and no DB rows for that ID. The roadmap refers to it on another machine (`G:\Meet2MoM`). It can't be frozen until the source data is provided.
