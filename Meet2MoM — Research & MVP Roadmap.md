# Meet2Mom — Research & MVP Roadmap

Oct 16, 2026 · @Sam

## Summary and the one priority order

Meet2Mom should sell one thing: **a meeting record you can audit**. Every decision carries its proposal and its agreement as separate transcript evidence, is checked by a verifier independent of the extractor, and is held for human review when the evidence is weak. Competitors already produce good summaries, action items and even decision lists, so features alone will not differentiate you; measured, visible verification can.

Follow the phases strictly in this order. Do not start a phase until the previous gate passes.

1. **Phase 0 — Ground truth and hygiene** (1 week): verify the code against your own claims, fix test leakage into `storage/`.
2. **Phase 1 — Semantic verification in the product** (2–3 weeks): the NLI layer runs in the pipeline and appears in Step 4 and Review. This is the top priority.
3. **Phase 2 — Evaluation and calibration** (2 weeks): adversarial catch rate, AMI benchmark, Hinglish set, data-driven thresholds.
4. **Phase 3 — Reviewer workflow and speed** (2 weeks): clickable audio evidence, persisted corrections, sign-off, async jobs, faster ASR.
5. **Phase 4 — Imports** (2–3 weeks): universal transcript-file import first, then Google Meet API import.
6. **Phase 5 — Pilot-ready MVP** (2–3 weeks): auth, tenancy, deletion, deployment, 3–5 pilot organisations.

Assuming a four-person team working part-time alongside coursework, this is 11–14 weeks. Zoom, Teams and live meeting bots come after the MVP, and only if pilots ask for them.

**How to read this doc.** Part A states only what your supplied files show. Part B is external research with sources. Part C is my recommendation. Where I could not verify something, I say so.

## Part A — Verified implementation status

Your own documentation is consistent with the folder tree: the semantic (NLI) verifier has run in tests and fixtures, but there is no sign it has ever run on `demo_01`. One limit first: you ran `tree` without `/F`, so it lists folders only. I could not see a single source file, so every "built" below means "folder exists and the demo script describes it", not "I read the code".

| Component | Evidence in what you sent | Status | Confirm in code |
| --- | --- | --- | --- |
| Upload / ingestion | `app/services/ingestion`; `recording`, `audio`, `chunks` per meeting | Built, slow (demo warns \~20 min per upload) | Is processing synchronous inside the request? |
| Transcription (Whisper) | `app/services/transcription`; demo: EN 95.2%, 0 low-confidence segments | Built | Model size and device; whether faster-whisper is used |
| Diarization (pyannote) | `app/services/diarization`; 5 speakers, 70 turns | Built | Pipeline version; overlap flag source |
| Transcript index, `utt_id`, foreign key | `app/services/transcript`, `app/schemas`; demo claims FK | Claimed built | Where the DB schema and FK are defined; no migrations folder is visible |
| Extraction (rules + Gemini) | `app/services/intelligence`; \~90 folders holding only `intelligence/llm_cache` | Built; both paths have run | Why so many cache-only folders exist (tests or failed runs?) |
| Deterministic verifier | `app/services/verification`; Step 4 screen text | Built, in UI | — |
| Semantic verifier (retrieval + NLI) | `verification/retrieval` in \~50 meetings; `verification/nli_cache` only in `cache-check-*`, `cited-none-*`, `mismatch-*` and `smoke_rules` | **Built, tested, not in pipeline output or UI** | Does the normal pipeline already run retrieval and stop before entailment? |
| Manual review | `frontend/src/pages`; Step 4b editable | Partial: edits not persisted | Where edits go on save |
| PDF minutes | `app/services/reporting`; `mom` folders | Built | — |
| Topic segmentation | Demo: 8 topics from 13 blocks | Built; labels weak ("Aren") | Tokenizer cutting words |
| Tests | `tests/`, `.pytest_cache`; demo claims 98 passing | Claimed | Re-run on a clean clone |
| AMI evaluation | `storage/ami_es2002a` exists, no `retrieval` or `nli_cache` inside | Not run through semantic verifier | — |

### Problems the tree reveals

- **Tests write into real `storage/`.** The `cache-check-<uuid>`, `cited-none-<uuid>` and `mismatch-<uuid>` folders (17+ each) are test fixtures leaking into production storage. Tests should use `tmp_path`.
- **Caches hold transcript text.** `llm_cache` and `nli_cache` copies of meeting content must be covered by any future "delete meeting" feature.
- **No auth, job queue, integration, deployment or CI folders** exist. That is expected at this stage, but it means Phases 3–5 are new work, not wiring.
- `member1_audio/` at the root looks like a leftover per-member prototype; confirm it is unused.

### Remaining work to finish the semantic verifier

1. Call retrieval + NLI from the main pipeline for every claim, not only from CLI/tests.
2. Persist one verification record per claim and method, with model version.
3. Expose it through the API that Step 4 and Review already call.
4. Render cited-span score, best-found span and a proposed correction in the UI.
5. Fuse NLI with ASR confidence, speaker confidence and overlap into one status with reasons.
6. Calibrate thresholds from data (Phase 2), not by hand.
7. Measure the added latency per claim on CPU.

To make the next review exact, run `tree /F /A app tests scripts frontend\src > tree.txt` and `pytest -q > pytest.txt`, and share both.

## Part B — Competitor research

Every major tool already extracts decisions and action items, and Google Meet now labels decisions with alignment statuses, so "we find decisions" is not a differentiator. Pricing below comes partly from third-party reviews; check vendor pages before quoting it in a pitch.

| Product | Strongest features | How it captures | Entry price (as reported) | Decisions / evidence |
| --- | --- | --- | --- | --- |
| [Otter.ai](https://sonix.ai/resources/otter-ai-review-pricing/) | Live transcript, AI chat over meeting history, Salesforce/HubSpot sync | Bot joins Zoom/Meet/Teams | Free 300 min/month; Business $19.99/user/month annual | Summaries and action items |
| [Fireflies.ai](https://www.toolsforhumans.ai/ai-tools/fireflies-ai) | 100+ languages, cross-meeting search assistant, many integrations, [MCP server](https://theaiagentindex.com/agents/fireflies-ai) | Bot | Pro from $10/user/month annual | Summaries with decisions and action items |
| [Fathom](https://www.techrepublic.com/article/news-best-ai-meeting-note-takers-2026/) | Free unlimited recording and transcription; strong free tier | Bot; [bot-free capture live for Zoom on Mac](https://www.laxis.com/blog/fathom-review/) | Free (advanced summaries capped at 5 calls/month) | Summaries, action items |
| [Granola](https://yespress.io/products/granola.md) | Notepad that merges your typed notes with the transcript; API and MCP; 32 languages | Device audio, no bot; [audio not retained](https://meetergo.com/en/magazine/granola-ai) | $14/user/month Business | Summaries, action items; [a proposed class action over consent was reported in July 2026](https://meetergo.com/en/magazine/granola-ai) |
| [Google Meet + Gemini](https://workspace.google.com/solutions/ai/ai-note-taking) | "Take notes for me" Doc, suggested next steps with owners, attached to Calendar | Native | Paid Workspace tiers; [Google AI Pro/Ultra since June 2026](https://blog.google/products-and-platforms/products/workspace/take-notes-for-me/) | [Decisions section with Aligned / Needs discussion / Disagreed / Shelved](https://tldv.io/ko/blog/gemini-google-meet); [notes reported to link to transcript points](https://eastleighvoice.co.ke/gemini%20ai/212852/google-gemini-ai-now-catches-you-up-on-what-you-missed-in-meetings) |
| [Zoom AI Companion](https://careers.zoom.us/blogs/zoomblog/meet-zoom-ai-companion-your-new-ai-assistant-unlock-the-benefits-with-a-paid-zoom-account) | Summaries, next steps, in-meeting "catch me up", smart recording chapters | Native | Included with paid Zoom plans | Action items and next steps |
| [Microsoft Teams](https://support.microsoft.com/teams/meetings/meeting-recap-app-teams) | Intelligent recap: AI notes, tasks, mentions; audio/video recaps | Native | Teams Premium or Microsoft 365 Copilot licence | Tasks and notes |

### What this means for Meet2Mom

- **Two demo claims are now unsafe.** "This is what nothing else on the market shows you" and "Google Meet doesn't show the evidence" conflict with reports that Gemini notes link to transcript points. Replace them with claims you can measure.
- **Gemini's decision statuses overlap your proposal-versus-agreement idea.** Your distinction must therefore be shown as evidence (two utterances, two speakers, timestamps), not as a label.
- **Native tools are free with the platform** (Zoom, and Meet on paid tiers). A startup cannot win on "summaries" against bundled features.

### Where Meet2Mom can realistically differentiate

I found no public documentation of the following in the products above. That is not proof they lack them; treat each as a hypothesis to verify in a hands-on trial before claiming it.

1. **Abstention as a feature:** unsupported decisions are withheld from the minutes and routed to review, with the reason shown.
2. **Independent re-verification:** a second retriever searches the whole transcript and flags mis-citations (your `u00030` case: cited 0.0004 vs found 0.94).
3. **Proposal and confirmation as separate evidence**, each with speaker and audio-reliability flags.
4. **Code-switched Indian meetings:** Google states Meet transcripts in languages other than English are not accurate ([source](https://support.google.com/meet/answer/12849897)). Verified Hinglish/Marathi minutes are a real gap if your numbers hold on a larger set.
5. **Platform-neutral audit trail:** the same verification on uploads, in-person recordings and imported Meet/Zoom/Teams transcripts, with reviewer sign-off and a published catch rate.

## Part B — Platform integrations

All three platforms let you import post-meeting transcripts through official APIs, but none is free end to end: each needs a paid plan or admin approval on the customer's side. Build the free universal path (transcript-file import) first, then Google Meet.

| Platform | Import transcript or recording? | What the customer must have | Your app's hurdles | Real-time option |
| --- | --- | --- | --- | --- |
| **Google Meet** | Yes. [`conferenceRecords.transcripts.entries`](https://developers.google.com/workspace/meet/api/guides/artifacts) gives per-participant text with timings and language code; recordings via Drive | Workspace edition with transcripts: [Business Standard/Plus, Enterprise, Education Plus, Workspace Individual](https://support.google.com/a/answer/12076932) | `meetings.space.readonly` is **sensitive** (app verification); `drive.readonly` is **restricted** and needs a security assessment if stored on servers ([source](https://developers.google.com/workspace/meet/api/guides/authenticate-authorize)). Entries are deleted 30 days after the meeting | Meet Media API exists; not recommended for you |
| **Zoom** | Yes. Cloud recording files including a transcript file, plus completion webhooks ([recording docs](https://developers.zoom.us/docs/video-sdk/web/recording/)) | Paid plan with cloud recording; [Zoom staff confirmed Pro includes audio transcripts](https://community.zoom.com/meetings-2/please-help-can-you-get-cloud-recording-transcripts-with-a-standard-pro-account-17249) | Apps used outside your own account must be [beta, unlisted or published, with Marketplace review](https://developers.zoom.us/docs/platform/key-concepts/) | [RTMS](https://developers.zoom.us/docs/rtms/contact-center/add-features/) streams audio/transcripts but needs paid Developer Pack credits |
| **Microsoft Teams** | Yes. [Graph `onlineMeeting` transcripts](https://learn.microsoft.com/en-us/graph/api/onlinemeeting-list-transcripts) return `text/vtt` ([content API](https://learn.microsoft.com/en-us/graph/api/calltranscript-get)) | Work/school account; transcription turned on; meeting on a calendar and not expired | `OnlineMeetingTranscript.Read.All`; app-only access needs a tenant **application access policy**; admins can disable Graph transcript access (403) | Teams media bots; out of scope |
| **Meeting bot vendor** | Yes, all platforms, one API | Nothing beyond the meeting link | [Recall.ai: $0.50/recording hour, +$0.15/hour transcription](https://recall.ai/blog/new-recall-ai-pricing-for-2026); [open-source Vexa reported as a self-host option](https://apicostcalc.com/recall-ai-vs-meetingbaas-vs-vexa-cost-calculator.html) | Visible bot; consent handling |
| **File upload / transcript file** | Already built for audio; add VTT/SRT/DOCX | Nothing | None | No |

### Answers to your five questions

1. **Can we import transcripts or recordings from existing meetings?** Yes, on all three platforms, for meetings where the customer enabled transcription or cloud recording. Meet transcript entries must be fetched within 30 days.
2. **Without platform fees?** Your API calls are free, but customers need paid plans (Workspace Business Standard+, Zoom Pro+, Teams with transcription). For your own testing you need such an account too; a college Workspace on a basic Education tier may not have transcripts. Zoom and Teams also add review or admin-consent steps.
3. **Real-time capture?** Possible (Zoom RTMS, Meet Media API, bot vendors), but it is a second product with paid access and constant platform breakage. Not practical for your team before the MVP.
4. **How does imported data enter the pipeline?** Through a normaliser that writes the same utterance rows your index uses, then skips ASR and diarization (Part C, Architecture).
5. **Which first?** Universal transcript-file import, then Google Meet. Reasons below.

### Why this order

- **File import is free and covers every platform today.** Zoom and Teams both export VTT; Meet exports a Doc. One parser serves all three plus offline meetings.
- **Meet's API fits your data model best.** Transcript entries arrive already attributed to a named participant with timings, which is better speaker evidence than diarization. You need only the sensitive `meetings.space.readonly` scope if you read entries, not the restricted Drive scope.
- **Meet is honest about your edge.** Its transcripts are English-focused, so imported Meet meetings show your verifier; your own Whisper path stays the route for Hinglish.
- **Zoom and Teams wait for demand.** Zoom needs Marketplace review for external users; Teams needs each customer's admin.

## Part C — Positioning and feature priorities

Position Meet2Mom as **verified minutes for meetings where the record matters**, not as another note-taker. Start with organisations that already write formal minutes and run code-switched meetings: college committees, housing-society and trust boards, SME management reviews, compliance and project-governance meetings. This beachhead is my hypothesis; test it with 10–15 short interviews during Phase 2.

**Pitch line that survives scrutiny:** "Every decision in your minutes links to who proposed it and who agreed, is checked by an independent verifier, and anything we cannot prove is held for review. Our measured catch rate is X%."

| Feature | Customer value | Feasibility for you | Existing progress | Priority |
| --- | --- | --- | --- | --- |
| Semantic verifier in product, with proposed corrections | Core promise | High | Built, not wired | **P0 — Phase 1** |
| Fused trust status with listed reasons | Explains every verdict | High | All inputs stored | **P0 — Phase 1** |
| Hold back unsupported claims (abstention) | Trust | High | Working in deterministic path | **P0 — Phase 1** |
| Proposal vs confirmation evidence | Distinguishes suggestion from agreement | High | Shown in Review | **P0 — keep** |
| Published catch rate (adversarial + AMI) | Proof for buyers and examiners | Medium | AMI file present | **P0 — Phase 2** |
| Persisted review, audit log, sign-off | Minutes become an approved record | High | Edit UI exists | **P0 — Phase 3** |
| Click evidence → play audio at that second | Fast human checking | High | Timestamps stored | **P0 — Phase 3** |
| Within-meeting contradiction and reversal ("we'll use X" → later "drop X") | Prevents stale decisions in minutes | Medium | NLI contradiction tested | **P1 — Phase 2/3** |
| Hinglish / Marathi verified minutes | Clear gap vs native tools | Medium | Small test (0.990) | **P1 — Phase 2 eval** |
| Transcript-file import (VTT/SRT/DOCX) | Works with any platform | High | — | **P1 — Phase 4** |
| Google Meet API import | Convenience for Workspace users | Medium | — | **P1 — Phase 4** |
| Evidence appendix in PDF and JSON export | Shareable proof | High | PDF exists | **P1 — Phase 3** |
| Proper topic titles | Polish | High | Keyword pairs | **P2 — Phase 3** |
| Zoom / Teams import | Wider reach | Low–medium (review, admin consent) | — | **After MVP** |
| Live capture, own bot, own ASR, CRM sync, multi-agent, cross-meeting knowledge graph | Low for this positioning | Low | — | **Not building** |

Keep the agent count at zero. Your pipeline is a fixed sequence; an "agent" adds cost and unpredictability without improving verification.

## Part C — Recommended architecture

Keep your current pipeline and add components around it; nothing needs rebuilding. Imports join at the transcript index, so extraction, verification and review work identically for every source.

&#91;embedded content: Meet2Mom architecture · existing path plus the components this roadmap adds\]

Uploaded media goes through Whisper and pyannote; imported transcripts skip that step and enter the index directly with platform speaker names and unknown ASR confidence.

- **Keep:** the Python backend served by `uvicorn app.api.main:app`, the React frontend, and the existing service folders.
- **Database:** keep your current engine locally; use PostgreSQL for the hosted MVP. Add `verification_results`, `corrections`, `jobs`, `organisations` and a `source` column on meetings.
- **Jobs:** a database job table and one worker process; add Redis/RQ only if one worker is not enough.
- **Models:** load Whisper, embeddings and NLI once per worker; store model and threshold versions with every verification row.
- **Storage:** local disk now; S3-compatible object storage in Phase 5, every path prefixed by organisation.

## Part C — The roadmap

Six phases take you from a verified demo to a pilot-ready MVP in 11–14 weeks. Effort is in person-weeks; each phase ends with a gate you must pass before starting the next.

&#91;embedded content: Roadmap · 6 phases, each closed by a gate\]

Phase 1 is the only highlighted band because nothing else matters until the semantic verifier is visible in the product.

### Phase 0 — Ground truth and hygiene (1 week, \~3 person-weeks)

**Objective:** every claim in your docs is tied to a file and a passing test.

- Produce `tree /F` and a module list; record the real test count from a clean clone.
- Locate the schema and the `claims → utterances` foreign key; add a test that inserting an ungrounded claim fails.
- Make all tests write to `tmp_path`; delete fixture folders from `storage/`; add `storage/`, `dist/`, `node_modules/`, caches to `.gitignore`.
- Freeze `demo_01` and `ami_es2002a` as golden fixtures with expected outputs.
- Write `STATUS.md`: feature, file, test, status (built / partial / claimed).
- Rename user-facing text to Meet2Mom in one place (config), not scattered strings.

**Inspect:** `app/schemas`, `app/core`, `tests/`, `scripts/`, `member1_audio/`. **Gate:** `pytest` passes on a fresh clone; `storage/` is unchanged after a test run; STATUS.md has no unverified "built" rows.

### Phase 1 — Semantic verification in the product (2–3 weeks, \~7 person-weeks)

**Objective:** the retrieval + NLI verifier runs on every claim and its result is visible and actionable in Step 4 and Review.

- **Pipeline:** after the deterministic verifier, run independent retrieval (embeddings + BM25) and entailment for each decision and action item. Load models once at startup; reuse `nli_cache`.
- **Storage:** a `verification_results` table — claim id, method, cited-span entailment, best-found span ids, best score, contradiction score, label, model and threshold version, timestamp.
- **Fusion v1 (rules, transparent):** combine deterministic status, cited-span entailment, best alternative, contradiction, ASR confidence, speaker confidence, overlap. Output one of Verified / Verified with warning / Review / Rejected plus a list of reasons. Missing inputs count as unknown, never as 1.0.
- **API:** extend the claims endpoint with the verification payload; no new page needed.
- **UI:** in Step 4 show both methods side by side; in Review show a "Suggested evidence" card when a better span is found, with Accept / Reject. Held-back claims appear in a visible "Needs review" list.

**Reuse:** `app/services/verification`, the existing Step 4 and Review components in `frontend/src/pages` and `components`. **New:** fusion module, results table, UI card. **Tests:** `demo_01` decision 2 is flagged with the corrected span; every `mismatch-*` style case produces a suggestion; no claim reaches export without a verification row; API contract tests. **Gate:** the `u00030` mis-citation is caught and shown in the UI on `demo_01`; added verification time per claim is measured and recorded.

### Phase 2 — Evaluation and calibration (2 weeks, \~6 person-weeks)

**Objective:** turn "it works on demos" into published numbers; this is also the research core of your final report.

- **Eval set:** `demo_01`, AMI ES2002a plus 2–3 more AMI meetings, and 5–10 consented Hinglish/Marathi recordings of your own. Two people label decisions, action items and evidence spans independently; measure their agreement.
- **Adversarial generator:** corrupt true claims by swapping owner, shifting deadline, negating outcome, moving the citation ±1 and ±3 utterances, and inventing a decision. Report catch rate per type.
- **Calibration:** choose thresholds that cap the false-verified rate on the dev split, then report on a held-out split. Store thresholds in versioned config.
- **Reversal detection:** test whether NLI contradiction between a decision and a later utterance flags reversals; keep it only if precision is acceptable.
- **Customer discovery:** 10–15 interviews with the beachhead users in parallel.

**Gate:** a one-page results table (catch rate, false-hold rate, extraction precision/recall vs AMI, Hinglish results) that you can quote.

### Phase 3 — Reviewer workflow and speed (2 weeks, \~7 person-weeks)

**Objective:** a reviewer can check and approve minutes quickly, and processing no longer blocks the browser.

- **Clickable evidence:** clicking any evidence line seeks the audio player to that timestamp.
- **Persisted review:** a `corrections` table (before, after, reviewer, reason, time) and an audit log; "Approve minutes" locks a version.
- **Export:** PDF and JSON with an evidence appendix (utterance ids, quotes, speakers, timestamps, status, verifier versions).
- **Async jobs:** upload returns a job id; a worker processes it; the UI polls progress. Start with a database job table and one worker process before adding Redis.
- **Speed:** benchmark [faster-whisper](https://github.com/SYSTRAN/faster-whisper) int8 on CPU against your current Whisper path; keep whichever meets accuracy. Your measured 19m19s for 4m08s of audio means a one-hour meeting would take over four hours today.
- **Topic titles:** fix word truncation; optionally generate titles with the LLM, constrained to topic utterances.

**Gate:** edits survive reload and appear in export; a 4-minute meeting processes in the background; new real-time factor recorded.

### Phase 4 — Imports (2–3 weeks, \~6 person-weeks)

**Objective:** meetings from any platform enter the same pipeline.

- **4a Transcript-file import:** parsers for Zoom VTT, Teams VTT, Meet Doc export, SRT and plain "Speaker: text". Map to utterance rows with deterministic ids; set ASR confidence to unknown; skip ASR and diarization; add a `source` field.
- **4b Google Meet API:** OAuth with `meetings.space.readonly` only; list conference records, transcripts and entries; map participants to speakers; "Import from Meet" button. Later, subscribe to `transcript.v2.fileGenerated` events via the [Workspace Events API](https://developers.google.com/workspace/events/guides/events-meet).

**Dependencies:** a Workspace account whose edition supports transcripts (trial or Workspace Individual). **Gate:** a real Meet meeting is imported and verified; re-import gives identical utterance ids; the same claims and statuses result as for file import of that meeting.

### Phase 5 — Pilot-ready MVP (2–3 weeks, \~7 person-weeks)

**Objective:** outside users can use Meet2Mom safely without you in the room.

- Google sign-in; organisations; roles (owner, reviewer, viewer); `org_id` on every table and every storage path.
- Consent notice on upload and import; delete-meeting that removes database rows, audio, chunks and all caches.
- An "LLM off" setting that runs rules-only extraction for sensitive meetings.
- Docker Compose (API, worker, PostgreSQL, static frontend); GitHub Actions running tests; logs that never contain transcript text.
- Pilot with 3–5 organisations; collect corrections as labelled data.

**Gate:** three external meetings processed and approved by non-team users; deletion verified end to end.

### After the MVP (only if pilots ask)

Zoom cloud-recording import, Teams Graph import, a Recall.ai bot for live capture, sharing approved minutes to email or Slack, and a simple cross-meeting decision ledger.

## Costs, security and risks

The MVP can run with near-zero API spend because ASR, diarization, embeddings and NLI are local models; your real cost is CPU time, and today that is about 4.7 minutes of processing per minute of audio.

### Expected costs

| Item | Free path | Paid path | Notes |
| --- | --- | --- | --- |
| Transcription and diarization | Local Whisper / faster-whisper + pyannote | GPU instance or hosted ASR | Dominant compute cost; fix in Phase 3 |
| Embeddings and NLI | Local sentence-transformers + mDeBERTa | — | Cheap per claim; measure in Phase 1 |
| LLM extraction | Rules-only extractor; Gemini free tier for development | Gemini paid tier | Read the Gemini API terms on data use before sending real customer meetings to any free tier |
| Hosting | Your laptop / college server for demo | One CPU VM + managed PostgreSQL | GPU only if pilots need fast turnaround |
| Google Meet testing | Workspace trial or Workspace Individual | Business Standard | Needed for Phase 4b |
| Zoom testing | — | Pro plan; RTMS needs Developer Pack credits | After MVP |
| Meeting bot | Self-hosted open-source bot | [Recall.ai $0.50/h + $0.15/h transcription](https://recall.ai/blog/new-recall-ai-pricing-for-2026) | After MVP |

### Security and privacy requirements (MVP)

- **Consent first:** a notice at upload/import that all participants agreed to recording and processing. Note the [reported consent lawsuit against Granola](https://meetergo.com/en/magazine/granola-ai); this is the category's main legal exposure. Design for India's Digital Personal Data Protection Act, 2023 (consent, purpose limitation, deletion on request) and get it reviewed before charging customers.
- **Least privilege:** Google `meetings.space.readonly` only; never `drive.readonly` unless you are ready for a security assessment. Encrypt stored OAuth refresh tokens.
- **Tenant isolation:** `org_id` on every row and storage path; tests that one org cannot read another's meeting.
- **Deletion:** removing a meeting deletes audio, chunks, transcript, claims, verification rows and all `llm_cache` / `nli_cache` / retrieval files.
- **LLM boundary:** an org-level switch to keep transcripts off third-party LLMs (rules-only extraction).
- **Logs and backups:** no transcript text in logs; backups encrypted and covered by deletion policy.
- **Audit trail:** every correction and approval is recorded with user and time.

### Major risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Native tools add evidence links and verification | Differentiator shrinks | Lead with measured catch rate and Indian code-switched meetings; re-check competitors each phase |
| NLI scores do not hold beyond small tests, especially Hinglish | Core claim fails | Phase 2 held-out evaluation before any public claim |
| CPU processing too slow for real meetings | Pilots abandon | faster-whisper, async jobs, optional GPU |
| Platform access gated by plans, review or admin consent | Integrations stall | File import first; Meet only after; others on demand |
| Scope creep (live mode, CRM, agents) | MVP never ships | The "Not building" list is binding until pilots ask |
| Overclaiming in viva or pitch | Credibility loss | Quote only Phase 2 numbers; remove "nothing else on the market" lines |
| Consent or data incident | Legal and reputational | Requirements above in Phase 5 before any external pilot |

## Final MVP feature list

The MVP is done when every item below works for an outside pilot user.

- [ ] Upload audio/video or import a transcript file (VTT, SRT, DOCX, text)
- [ ] Import from Google Meet by OAuth
- [ ] Indexed transcript with permanent utterance ids, speakers, timestamps and reliability flags
- [ ] Decision and action-item extraction (rules and LLM), every item grounded in utterance ids
- [ ] Deterministic and semantic verification on every claim, with suggested evidence corrections
- [ ] One fused status per claim with listed reasons; unsupported claims held for review
- [ ] Proposal and confirmation shown as separate evidence
- [ ] Click evidence to play audio at that moment
- [ ] Persisted corrections, audit log and "Approve minutes" sign-off
- [ ] PDF and JSON minutes with an evidence appendix
- [ ] Background processing with progress
- [ ] Sign-in, organisations, roles, consent notice, full deletion, LLM-off switch
- [ ] Published evaluation: catch rate, false-hold rate, AMI and Hinglish results

**Explicitly not in the MVP:** live meeting capture, own meeting bot, Zoom and Teams connectors, CRM/Jira/Slack sync, multi-agent workflows, cross-meeting knowledge graph, training your own ASR.

## Sources

All pages opened during this research on 16 October 2026.

**Official platform documentation**

- [Google Meet REST API — work with artifacts](https://developers.google.com/workspace/meet/api/guides/artifacts)
- [Google Meet REST API — authentication and scopes](https://developers.google.com/workspace/meet/api/guides/authenticate-authorize)
- [Google Workspace Events API — Meet events](https://developers.google.com/workspace/events/guides/events-meet)
- [Google Meet transcripts — user help](https://support.google.com/meet/answer/12849897)
- [Google Meet transcripts — supported editions](https://support.google.com/a/answer/12076932)
- [Google: Take notes for me for AI Pro/Ultra](https://blog.google/products-and-platforms/products/workspace/take-notes-for-me/)
- [Google Workspace AI note-taking](https://workspace.google.com/solutions/ai/ai-note-taking)
- [Zoom cloud recording and transcript webhooks](https://developers.zoom.us/docs/video-sdk/web/recording/)
- [Zoom RTMS — add features](https://developers.zoom.us/docs/rtms/contact-center/add-features/)
- [Zoom RTMS general availability](https://developers.zoom.us/changelog/rtms/june-25-2025/)
- [Zoom platform key concepts (private, beta, published apps)](https://developers.zoom.us/docs/platform/key-concepts/)
- [Zoom community: Pro includes audio transcripts](https://community.zoom.com/meetings-2/please-help-can-you-get-cloud-recording-transcripts-with-a-standard-pro-account-17249)
- [Zoom AI Companion](https://careers.zoom.us/blogs/zoomblog/meet-zoom-ai-companion-your-new-ai-assistant-unlock-the-benefits-with-a-paid-zoom-account)
- [Microsoft Graph — list meeting transcripts](https://learn.microsoft.com/en-us/graph/api/onlinemeeting-list-transcripts)
- [Microsoft Graph — get transcript content](https://learn.microsoft.com/en-us/graph/api/calltranscript-get)
- [Microsoft Teams Meeting Recaps licensing](https://support.microsoft.com/teams/meetings/meeting-recap-app-teams)
- [Recall.ai 2026 pricing](https://recall.ai/blog/new-recall-ai-pricing-for-2026)

**Third-party reviews (verify before quoting)**

- [Otter.ai review 2026 (Sonix)](https://sonix.ai/resources/otter-ai-review-pricing/)
- [Fireflies.ai review (Tools for Humans)](https://www.toolsforhumans.ai/ai-tools/fireflies-ai)
- [Fireflies.ai profile (AI Agent Index)](https://theaiagentindex.com/agents/fireflies-ai)
- [Best AI meeting note takers 2026 (TechRepublic)](https://www.techrepublic.com/article/news-best-ai-meeting-note-takers-2026/)
- [Fathom review (Laxis)](https://www.laxis.com/blog/fathom-review/)
- [Granola profile (Yespress)](https://yespress.io/products/granola.md)
- [Granola AI review (meetergo)](https://meetergo.com/en/magazine/granola-ai)
- [Gemini in Google Meet guide (tl;dv)](https://tldv.io/ko/blog/gemini-google-meet)
- [Gemini meeting catch-up report (Eastleigh Voice)](https://eastleighvoice.co.ke/gemini%20ai/212852/google-gemini-ai-now-catches-you-up-on-what-you-missed-in-meetings)
- [Meeting bot API cost comparison](https://apicostcalc.com/recall-ai-vs-meetingbaas-vs-vexa-cost-calculator.html)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
