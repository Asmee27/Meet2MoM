"""
Train the Phase 2 dialogue model on AMI (train split) and pick decoding
thresholds on AMI dev. The test split (TS meetings) is never read here.

    python -m evaluation.train_dialogue_model

Writes app/services/intelligence/models/dialogue_model_v1.npz (+ .json report).
"""
from __future__ import annotations

import itertools
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, f1_score
from sklearn.preprocessing import StandardScaler

from app.services.intelligence import dialogue_model as dm
from evaluation.embed_ami import EMB_DIR
from evaluation.harness import AMI_DERIVED, GoldItem, Case, aggregate, score_case

import os
VERSION = os.getenv("DIALOGUE_VERSION", "dialogue-v1")
USE_POSITION = os.getenv("DIALOGUE_USE_POSITION", "1") == "1"
OUTPUT = Path(os.getenv("DIALOGUE_OUTPUT", str(dm.MODEL_PATH)))
SPEECH_ACT_MAP = {
    "suggest": "suggest", "offer": "offer", "inform": "inform", "assess": "assess",
    "elicit_inform": "elicit", "elicit_offer_suggestion": "elicit", "elicit_assessment": "elicit",
    "elicit_understanding": "elicit", "be_positive": "positive", "be_negative": "negative",
    "backchannel": "minor", "stall": "minor", "fragment": "minor",
}
POLARITY = ("POS", "NEG", "PART", "UNC")
# Regularization: speech_act/decision/action values were selected on dev in the
# first training run (see the .json report); polarity is searched again because
# its label set gained NONE.
C_GRID = {"speech_act": (0.003,), "decision": (0.003,), "action": (0.1,), "polarity": (0.003, 0.01, 0.03)}


@dataclass
class U:
    utt_id: str
    text: str
    speaker_id: str


def load(split: str):
    meetings = []
    for path in sorted(AMI_DERIVED.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["split"] != split:
            continue
        emb = np.load(EMB_DIR / f"{path.stem}.npy")
        meetings.append((data, emb))
    return meetings


def build_rows(meetings):
    X, y_sa, y_dec, y_act, P, y_pol = [], [], [], [], [], []
    for data, emb in meetings:
        utts = data["utterances"]
        texts = [u["text"] for u in utts]
        speakers = [u["speaker"] for u in utts]
        index = {u["utt_id"]: i for i, u in enumerate(utts)}
        X.append(dm.context_features(emb, texts, speakers, USE_POSITION))
        y_sa += [SPEECH_ACT_MAP.get(u["da_label"], "other") for u in utts]
        dec = {e for g in data["gold"] if g["item_type"] == "decision" for e in g["evidence"]}
        act = {e for g in data["gold"] if g["item_type"] == "action" for e in g["evidence"]}
        y_dec += [int(u["utt_id"] in dec) for u in utts]
        y_act += [int(u["utt_id"] in act) for u in utts]
        replied = {}
        for source, target, kind in data["adjacency"]:
            if kind in POLARITY and source in index and target in index and index[target] > index[source]:
                P.append(dm.pair_features(emb, index[target], index[source], speakers, texts))
                y_pol.append(kind)
                replied.setdefault(index[source], set()).add(index[target])
        # NONE: nearby turns by other speakers that annotators did NOT link as a
        # reply to the proposal/statement. Teaches "is this a reply at all?".
        anchors = set(replied) | {index[e] for g in data["gold"] for e in g["evidence"] if e in index}
        for t in sorted(anchors):
            for r in range(t + 1, min(len(utts), t + 4)):
                if speakers[r] != speakers[t] and r not in replied.get(t, set()):
                    P.append(dm.pair_features(emb, r, t, speakers, texts))
                    y_pol.append("NONE")
    return (np.vstack(X), np.array(y_sa), np.array(y_dec), np.array(y_act),
            np.vstack(P) if P else np.zeros((0, 1)), np.array(y_pol))


def fit(X, y, C):
    scaler = StandardScaler().fit(X)
    clf = LogisticRegression(C=C, max_iter=3000, class_weight="balanced")
    clf.fit(scaler.transform(X), y)
    return scaler, clf


def head_arrays(name, scaler, clf):
    scale = scaler.scale_.copy()
    scale[scale == 0] = 1.0
    return {
        f"{name}__coef": clf.coef_.astype(np.float32),
        f"{name}__intercept": clf.intercept_.astype(np.float32),
        f"{name}__mean": scaler.mean_.astype(np.float32),
        f"{name}__scale": scale.astype(np.float32),
    }, [str(c) for c in clf.classes_]


def decode_meeting(model, data, emb):
    utts = [U(u["utt_id"], u["text"], u["speaker"]) for u in data["utterances"]]
    cands = dm.decode(model, utts, emb)
    claims = []
    for k, c in enumerate(cands):
        payload = {key: c.get(key) for key in ("state", "owner", "owner_type", "deadline_text", "commitment_type")}
        claims.append({"claim_id": f"{data['meeting_id']}_{k}", "claim_type": c["claim_type"],
                       "evidence": c["evidence"], "payload": payload, "text": c["text"]})
    return claims


def case_of(data) -> Case:
    gold = [GoldItem(g["item_id"], g["item_type"], g["text"], g["evidence"]) for g in data["gold"] if g["evidence"]]
    return Case(data["meeting_id"], "ami", data["split"], [], gold)


def main() -> int:
    started = time.perf_counter()
    report: dict = {"version": VERSION, "train_split": "AMI ES (train)", "dev_split": "AMI IS (dev)"}
    train, dev = load("train"), load("dev")
    Xtr, sa_tr, dec_tr, act_tr, Ptr, pol_tr = build_rows(train)
    Xdv, sa_dv, dec_dv, act_dv, Pdv, pol_dv = build_rows(dev)
    report["rows"] = {"train_utterances": int(len(Xtr)), "dev_utterances": int(len(Xdv)),
                      "train_pairs": int(len(Ptr)), "dev_pairs": int(len(Pdv)),
                      "train_decision_pos": int(dec_tr.sum()), "train_action_pos": int(act_tr.sum())}
    print(report["rows"], flush=True)

    arrays, classes, head_report = {}, {}, {}
    # C chosen on dev per head
    for name, (Xa, ya, Xb, yb, metric) in {
        "speech_act": (Xtr, sa_tr, Xdv, sa_dv, "macro_f1"),
        "decision": (Xtr, dec_tr, Xdv, dec_dv, "ap"),
        "action": (Xtr, act_tr, Xdv, act_dv, "ap"),
        "polarity": (Ptr, pol_tr, Pdv, pol_dv, "macro_f1"),
    }.items():
        best = None
        for C in C_GRID[name]:
            scaler, clf = fit(Xa, ya, C)
            pred = clf.predict_proba(scaler.transform(Xb))
            if metric == "ap":
                value = average_precision_score(yb, pred[:, 1])
            else:
                value = f1_score(yb, clf.classes_[pred.argmax(1)], average="macro")
            print(f"{name} C={C}: dev {metric}={value:.4f}", flush=True)
            if best is None or value > best[0]:
                best = (value, C, scaler, clf)
        value, C, scaler, clf = best
        a, cls = head_arrays(name, scaler, clf)
        arrays.update(a)
        classes[name] = cls
        head_report[name] = {"C": C, f"dev_{metric}": round(float(value), 4)}
        if metric == "ap":
            head_report[name]["dev_base_rate"] = round(float(np.mean(yb)), 4)
    report["heads"] = head_report

    # ---- thresholds: grid search on dev, objective = mean of decision and action F1
    # (evidence-near matching), with accepted-claim precision recorded alongside.
    meta = {"version": VERSION, "heads": list(classes), "classes": classes, "thresholds": {},
            "features": {"use_position": USE_POSITION}}

    def model_with(th):
        path = Path(__file__).parent / "_tmp_model.npz"
        m = dict(meta, thresholds=th)
        np.savez(path, meta=json.dumps(m), **arrays)
        model = dm.DialogueModel(path)
        path.unlink()
        return model

    # Cache per-meeting head scores and reply probabilities once.
    base_model = model_with({"decision": 0.5, "action": 0.5, "pos": 0.5, "neg": 0.5, "part": 0.5,
                             "response_window": 3, "min_tokens": 3})
    cached = []
    for data, emb in dev:
        utts = [U(u["utt_id"], u["text"], u["speaker"]) for u in data["utterances"]]
        sc = dm.score_utterances(base_model, emb, [u.text for u in utts], [u.speaker_id for u in utts])
        cached.append((data, emb, utts, sc, {}))

    baseline_path = Path(__file__).parent / "results" / "baseline_rules_v2_ami_dev.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["per_set"]["ami-dev"]
    max_false = baseline["confirmations"]["false_confirmations"]
    report["constraint"] = f"false_confirmations <= rules-v2 dev baseline ({max_false})"

    grid = []
    for td, ta, pos in itertools.product((0.8, 0.9, 0.95, 0.97), (0.8, 0.9, 0.95), (0.5, 0.6, 0.7, 0.8)):
        th = {"decision": td, "action": ta, "pos": pos, "neg": 0.5, "part": 0.5,
              "response_window": 3, "min_tokens": 3}
        base_model.thresholds = th
        scores = []
        for data, emb, utts, sc, pc in cached:
            cands = dm.decode(base_model, utts, emb, scores=sc, pair_cache=pc)
            claims = [{"claim_id": f"{data['meeting_id']}_{k}", "claim_type": c["claim_type"], "evidence": c["evidence"],
                       "payload": {key: c.get(key) for key in ("state", "owner", "owner_type", "deadline_text", "commitment_type")},
                       "text": c["text"]} for k, c in enumerate(cands)]
            scores.append(score_case(case_of(data), claims))
        agg = aggregate(scores)
        f1 = (agg["decision_near"]["f1"] + agg["action_near"]["f1"]) / 2
        false_conf = agg["confirmations"]["false_confirmations"]
        grid.append((false_conf <= max_false, f1, -false_conf, th, agg))
        print(f"td={td} ta={ta} pos={pos}: dec F1={agg['decision_near']['f1']} act F1={agg['action_near']['f1']} "
              f"claims/mtg={agg['claims_per_meeting']} false_conf={false_conf}", flush=True)
    # feasible first, then F1, then fewer false confirmations
    grid.sort(key=lambda g: (g[0], g[1], g[2]), reverse=True)
    feasible, best_f1, _, best_th, best_agg = grid[0]
    report["constraint_satisfied"] = bool(feasible)
    meta["thresholds"] = best_th
    report["thresholds"] = best_th
    report["dev_at_thresholds"] = best_agg
    report["seconds"] = round(time.perf_counter() - started, 1)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez(OUTPUT, meta=json.dumps(meta), **arrays)
    OUTPUT.with_suffix(".json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
