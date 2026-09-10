"""Meeting diagnostic report. Usage: python scripts/report_meeting.py <meeting_id>"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from statistics import mean


MEETING_ID = sys.argv[1] if len(sys.argv) > 1 else "ami_es2002a"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DB = str(PROJECT_ROOT / "storage" / "meet2mom.db")


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def safe(label, fn):
    try:
        return fn()
    except Exception:
        print(f"[FAILED] {label}")
        traceback.print_exc(limit=3)
        return None


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
one = lambda q, p=(): conn.execute(q, p).fetchone()[0]
rows = lambda q, p=(): [dict(r) for r in conn.execute(q, p)]
M = (MEETING_ID,)


# ---------------------------------------------------------------- utterances
section(f"A. UTTERANCES — {MEETING_ID}")


def _utt():
    n = one("SELECT COUNT(*) FROM utterances WHERE meeting_id=?", M)
    print("count:", n)
    if n == 0:
        print("!! NO UTTERANCES. Pipeline did not reach the index stage.")
        return 0
    print(
        "speakers:",
        one("SELECT COUNT(DISTINCT speaker_id) FROM utterances WHERE meeting_id=?", M),
    )
    print("duration_s: %.1f" % one("SELECT MAX(end) FROM utterances WHERE meeting_id=?", M))
    print(
        "conf_source:",
        rows(
            "SELECT conf_source,COUNT(*) n FROM utterances "
            "WHERE meeting_id=? GROUP BY conf_source",
            M,
        ),
    )
    for col in ("asr_conf", "spk_conf"):
        r = conn.execute(
            f"SELECT MIN({col}) lo,MAX({col}) hi,AVG({col}) avg,"
            f"COUNT(DISTINCT {col}) d FROM utterances "
            f"WHERE meeting_id=? AND {col} IS NOT NULL",
            M,
        ).fetchone()
        print(f"{col}: min={r['lo']} max={r['hi']} avg={r['avg']} distinct={r['d']}")
    print("overlap:", one("SELECT COUNT(*) FROM utterances WHERE meeting_id=? AND overlap=1", M))
    print(
        "no_speech>0.5:",
        one(
            "SELECT COUNT(*) FROM utterances "
            "WHERE meeting_id=? AND no_speech_prob>0.5",
            M,
        ),
    )
    print("\n-- first 3 utterances (check for mid-sentence fragments) --")
    for r in rows(
        "SELECT utt_id,speaker_id,start,text FROM utterances "
        "WHERE meeting_id=? ORDER BY start LIMIT 3",
        M,
    ):
        print(f"  [{r['utt_id']}] {r['speaker_id']} {r['start']:.1f}s: {r['text'][:90]}")
    return n


n_utts = safe("utterances", _utt)


# -------------------------------------------------------------------- windows
section("B. EXTRACTION WINDOWS  (>1 required)")


def _win():
    from app.core import config
    from app.services.intelligence.context_builder import build_windows

    if hasattr(config, "set_active_meeting"):
        config.set_active_meeting(MEETING_ID)
    windows = build_windows(MEETING_ID)
    sizes = [len(window.utt_ids) for window in windows]
    print(
        f"windows={len(windows)}  mean={mean(sizes) if sizes else 0:.1f}  "
        f"min={min(sizes) if sizes else 0} max={max(sizes) if sizes else 0}"
    )
    if len(windows) <= 1 and (n_utts or 0) > 100:
        print("!! ONLY 1 WINDOW WITH >100 UTTERANCES — build_windows is not splitting.")
    return windows


safe("windows", _win)


# --------------------------------------------------------------------- claims
section("C. CLAIMS & INVARIANT")


def _claims():
    print(
        "by type:",
        rows(
            "SELECT claim_type,COUNT(*) n FROM claims "
            "WHERE meeting_id=? GROUP BY claim_type",
            M,
        ),
    )
    print(
        "drops:",
        rows(
            "SELECT reason,COUNT(*) n FROM dropped_claims "
            "WHERE meeting_id=? GROUP BY reason",
            M,
        ),
    )
    accepted = one("SELECT COUNT(*) FROM claims WHERE meeting_id=?", M)
    dropped = one("SELECT COUNT(*) FROM dropped_claims WHERE meeting_id=?", M)
    print(
        f"accepted={accepted} dropped={dropped} "
        f"drop_rate={dropped/(accepted+dropped) if accepted+dropped else 0:.3f}"
    )
    orphaned = one(
        "SELECT COUNT(*) FROM claims c LEFT JOIN evidence_links e "
        "ON c.claim_id=e.claim_id WHERE c.meeting_id=? AND e.claim_id IS NULL",
        M,
    )
    print(
        f"ORPHAN CLAIMS: {orphaned}   "
        f"{'OK' if orphaned == 0 else '!! INVARIANT VIOLATED'}"
    )
    texts = Counter()
    for row in rows(
        "SELECT claim_type,LOWER(TRIM(text)) t FROM claims WHERE meeting_id=?",
        M,
    ):
        texts[row["t"][:60]] += 1
    dupes = {key: value for key, value in texts.items() if value > 1}
    print("duplicate claim texts across types:", dupes if dupes else "none")
    return accepted


safe("claims", _claims)


section("D. CLAIMS WITH EVIDENCE TEXT (first 10)")


def _show():
    utts = {
        row["utt_id"]: row
        for row in rows(
            "SELECT utt_id,speaker_id,start,text FROM utterances WHERE meeting_id=?",
            M,
        )
    }
    for claim in rows(
        "SELECT claim_id,claim_type,text,payload FROM claims "
        "WHERE meeting_id=? LIMIT 10",
        M,
    ):
        payload = json.loads(claim["payload"] or "{}")
        if claim["claim_type"] == "action":
            extra = (
                f"  owner={payload.get('owner')} ({payload.get('owner_type')}) "
                f"deadline={payload.get('deadline_resolved')} "
                f"({payload.get('deadline_type')})"
            )
        else:
            extra = f"  state={payload.get('state')}"
        print(f"\n[{claim['claim_type']}] {claim['text']}{extra}")
        for evidence in rows(
            "SELECT utt_id FROM evidence_links WHERE claim_id=?",
            (claim["claim_id"],),
        ):
            utterance = utts.get(evidence["utt_id"])
            if utterance:
                print(
                    f"   {utterance['utt_id']} ({utterance['speaker_id']}, "
                    f"{utterance['start']:.0f}s) \"{utterance['text'][:100]}\""
                )
    print("\n-- dropped (first 10) --")
    for dropped in rows(
        "SELECT reason,text,cited_evidence FROM dropped_claims "
        "WHERE meeting_id=? LIMIT 10",
        M,
    ):
        print(
            f"  {dropped['reason']} | {(dropped['text'] or '')[:70]} | "
            f"{dropped['cited_evidence']}"
        )


safe("claim display", _show)


# ------------------------------------------------------------------ retrieval
section("E. RETRIEVAL INDEX")


def _ret():
    from app.services.verification.retrieval_index import RetrievalIndex

    started = time.perf_counter()
    index = RetrievalIndex(MEETING_ID)
    index.build()
    print(
        f"spans={len(index.spans)} dim={index._emb.shape[1]} "
        f"build={time.perf_counter()-started:.1f}s"
    )


safe("retrieval", _ret)


# ------------------------------------------------------------------------ NLI
section("F. NLI VERIFICATION")


def _nli():
    from app.services.verification.nli_scorer import score_meeting

    started = time.perf_counter()
    results = score_meeting(MEETING_ID)
    print(f"scored {len(results)} claims in {time.perf_counter()-started:.1f}s\n")
    header = (
        "claim_id",
        "e_bg",
        "e_cited",
        "cit_marg",
        "e_union",
        "uni_marg",
        "cov",
        "x_union",
        "mism",
    )
    print(" | ".join(f"{item:>9}" for item in header))
    claim_texts = {
        row["claim_id"]: row["text"]
        for row in rows("SELECT claim_id,text FROM claims WHERE meeting_id=?", M)
    }
    deltas = []
    for result in results:
        get = lambda attribute: getattr(result, attribute, None)
        deltas.append((get("e_union") or 0) - (get("e_cited") or 0))
        values = [
            result.claim_id[-9:],
            get("e_background"),
            get("e_cited"),
            get("e_cited_margin"),
            get("e_union"),
            get("e_union_margin"),
            get("content_coverage"),
            get("x_union"),
            get("evidence_mismatch"),
        ]
        print(
            " | ".join(
                f"{value:>9.3f}"
                if isinstance(value, float)
                else f"{str(value):>9}"
                for value in values
            )
        )
        print(f"            text: {result.claim_id} :: {claim_texts.get(result.claim_id, '')[:60]}")
        print(
            f"            best={get('best_span_utt_ids')} "
            f"proposed={get('proposed_utt_ids')}"
        )
    if deltas:
        print(
            f"\n(e_union - e_cited): min={min(deltas):.3f} "
            f"max={max(deltas):.3f} mean={mean(deltas):.3f} "
            "-> use to tune MISMATCH_DELTA"
        )
        mismatched = sum(
            1 for result in results if getattr(result, "evidence_mismatch", False)
        )
        print(f"mismatch flagged: {mismatched}/{len(results)}")


safe("nli", _nli)

conn.close()
print("\n" + "=" * 70)
print("REPORT COMPLETE")
print("=" * 70)
