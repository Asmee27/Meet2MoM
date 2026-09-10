# Real Recording Baseline Report

Date: 2026-09-10  
Repository: `G:\Meet2MoM`  
Recording: `input/test_meeting.mp4`  
Duration: **248.13 seconds (4.14 minutes)**  
Size: **10,407,429 bytes**

## Environment and token verification

The repository `.env` contains configured values for both accepted Hugging Face
variable names. No token value was printed or logged.

The resolver check succeeded:

```text
HF token resolved: YES | pyannote pipeline loaded: YES
```

The LLM configuration used for this measurement was the permitted stub
provider:

```text
LLM_PROVIDER=stub
EXTRACTOR=rules / llm
```

The stub JSON call succeeded. No live LLM API call was made because no live
provider/API-key configuration was used for this run.

## Recorded pipeline timing

The existing recorded runner executes stages 1–9 through report generation.
Index, topics, and extraction are separate existing orchestration stages and
were then run using the existing index/topic/extraction modules.

| Stage | `smoke_rules` | `smoke_llm` |
|---|---:|---:|
| Validation | completed | completed |
| Media inspection | completed | completed |
| Audio enhancement | completed | completed |
| Audio quality | completed | completed |
| Chunk preparation | completed | completed |
| ASR | cache hit; skipped Whisper | cache hit; skipped Whisper |
| Diarization | 1273.99 s | 1730.72 s |
| Speaker merge/reliability | completed | completed |
| Report generation | completed | completed |
| Recorded-run wall time | **1340.55 s** | **1889.45 s** |
| Index + topics + extraction | measured separately | measured separately |
| Extraction only | **2.37 s** | **1.13 s** |

The second meeting run confirmed an ASR cache hit. ASR was not rerun. The
diarization cache is meeting-specific, so diarization ran independently for
the distinct `smoke_rules` and `smoke_llm` IDs.

ASR real-time ratio:

```text
smoke_rules: N/A for this rerun because ASR was a cache hit
smoke_llm:   N/A for this rerun because ASR was a cache hit
```

The original uncached ASR measurement for this recording was 427.29 seconds
for 248.13 seconds of audio, giving a real-time ratio of **1.722**. That
uncached timing was shared by the original recording and was not repeated
after the cache became valid.

## Numeric baseline

### Utterances

| Metric | `smoke_rules` | `smoke_llm` |
|---|---:|---:|
| Utterances | 39 | 39 |
| `conf_source=numeric` | 39 | 39 |
| `conf_source=bucketed` | 0 | 0 |
| `conf_source=missing` | 0 | 0 |
| ASR min | 0.5262 | 0.5262 |
| ASR max | 0.8986 | 0.8986 |
| ASR mean | 0.8559667 | 0.8559667 |
| Distinct ASR values | 9 | 9 |
| Speaker min | 0.2934 | 0.2934 |
| Speaker max | 1.0 | 1.0 |
| Speaker mean | 0.7502385 | 0.7502385 |
| Distinct speaker values | 37 | 37 |
| `overlap=1` | 13 | 13 |
| `no_speech_prob > 0.5` | 5 | 5 |

### Windows

Both meetings produced one extraction window:

| Metric | `smoke_rules` | `smoke_llm` |
|---|---:|---:|
| Window count | 1 | 1 |
| Mean utterances/window | 39 | 39 |
| Minimum | 39 | 39 |
| Maximum | 39 | 39 |

### Claims, drops, and evidence

| Metric | `smoke_rules` | `smoke_llm` |
|---|---:|---:|
| Decision claims | 2 | 0 |
| Action claims | 0 | 0 |
| Dropped claims | 0 | 0 |
| Drop rate | 0.0 | 0.0 |
| Claims without evidence | **0** | **0** |
| Mean evidence spans/claim | 1.0 | 0 |
| Claims with exactly one span | 2 | 0 |

The required orphan invariant query returned **zero rows for both meetings**.

### Action resolution

There were no persisted action claims in either run, so action-only
resolution distributions and means are empty/N/A:

| Metric | `smoke_rules` | `smoke_llm` |
|---|---|---|
| Owner-type distribution | `{}` | `{}` |
| Owner resolved / total | 0 / 0 | 0 / 0 |
| Deadline-type distribution | `{}` | `{}` |
| Deadline resolved count | 0 | 0 |
| Mean owner resolution confidence | N/A | N/A |
| Mean deadline resolution confidence | N/A | N/A |

## Actual extracted content

### Rules mode — `smoke_rules`

```text
[decision] Start with a review of the current training approach and look at what we need to do to address the problems brought up in the survey.  state=accepted
  evidence: u00030 (SPEAKER_01, 3:15) "your staff. So I wondered if we could start with a review of the current"

[decision] Look at the report together.  state=accepted
  evidence: u00033 (SPEAKER_01, 3:31) "the full report first, of course. Anna, I understand your point about"
```

Dropped claims: none.

### LLM stub mode — `smoke_llm`

```text
No claims produced.
```

Dropped claims: none.

## `no_speech_prob`

`no_speech_prob` reaches the utterances table as a nullable `REAL` column.
The real recording produced 5 utterances above 0.5 in both meetings. The
schema, index mapping, database migration, and round-trip test are active.

## Defect assessment

Rules mode did not produce zero claims: it accepted 2 claims with a zero drop
rate. The LLM-stub mode produced zero claims because the existing stub provider
returns an empty `{"decisions": [], "actions": []}` response unless a test
response is explicitly registered. This is expected stub behavior, not a
validator or persistence failure. The live LLM baseline remains unmeasured.

The prior diarization failure was fixed by centralized dotenv loading and
alias resolution through `config.get_hf_token()`.

## Validation

```text
python -m pytest tests/ -q
54 passed
```

## Unresolved issues

1. The LLM figures are stub-provider measurements, not live-provider
   measurements.
2. The existing runner does not emit individual wall-clock durations for every
   early stage; the report records the exact available total and diarization
   timings, plus separately measured extraction time.
## Task 3.0 carry-over validation

- The recorded pipeline now invokes the shared index -> topics -> extraction
  path automatically, and the background job runner uses that same helper.
- Transcript index persistence is idempotent: evidence links and claims are
  removed before utterances are replaced.
- The Gemini provider was added to the existing retrying `LLMClient`. The
  configured `gemini-2.0-flash` request reached Gemini but returned HTTP 404
  because that model is no longer available for this API account. The
  extraction fallback therefore ran in rules mode and accepted 2 claims.
- The synthetic 300-utterance multi-window test passed, including six windows,
  ten-utterance overlap, out-of-window rejection, and duplicate rejection.
- Full test suite: **56 passed**.
