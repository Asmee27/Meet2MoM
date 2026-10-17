"""
Context-aware dialogue extractor (Phase 2).

A learned alternative to keyword rules for finding decision and action
candidates and their state. Every utterance is represented by a multilingual
sentence embedding (the same model the retrieval index uses), the embeddings of
its neighbouring utterances, and a few structural features. Linear heads
trained on human annotations of the AMI Meeting Corpus (train split only; see
evaluation/train_dialogue_model.py) predict:

  speech_act : suggest / offer / inform / assess / elicit / positive / negative / minor / other
  decision   : P(utterance is evidence for a meeting decision)
  action     : P(utterance is evidence for an action item)
  polarity   : whether a later utterance replies to a candidate, and how:
               NONE (not a reply) / POS / NEG / PART / UNC

Decoding uses dialogue context, not phrases:
  - a decision is "accepted" only if a DIFFERENT speaker responds positively,
    "rejected" on an objection, "conditional" on partial/conditional agreement,
    otherwise "proposed";
  - an action is "firm" only when someone else accepts it, otherwise "tentative".
Owner and deadline come from the existing ActionItemExtractor helpers and are
left empty when the text does not state them.

The weights are plain numpy arrays (no pickle). Thresholds live in the model
file's metadata and were chosen on the AMI dev split, never on test data.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_PATH = Path(
    os.getenv("DIALOGUE_MODEL_PATH") or Path(__file__).parent / "models" / "dialogue_model_v1.npz"
)
STRUCT_FEATURES = ("is_question", "speaker_change_prev", "speaker_change_next",
                   "rel_position", "log_tokens", "short", "first_person", "second_person", "plural_first")


# ---------------------------------------------------------------------------
# Features (shared by training and inference)
# ---------------------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower(), flags=re.UNICODE)


def structural_features(texts: list[str], speakers: list[str], use_position: bool = True) -> np.ndarray:
    n = len(texts)
    rows = []
    for i, text in enumerate(texts):
        toks = _tokens(text)
        tset = set(toks)
        rows.append([
            1.0 if text.strip().endswith("?") else 0.0,
            1.0 if i == 0 or speakers[i - 1] != speakers[i] else 0.0,
            1.0 if i == n - 1 or speakers[i + 1] != speakers[i] else 0.0,
            # relative position only helps on long meetings; models that do not
            # use it store use_position=False in their metadata
            (i / max(1, n - 1)) if use_position else 0.0,
            float(np.log1p(len(toks))),
            1.0 if len(toks) <= 3 else 0.0,
            # pronoun person is grammatical structure (who acts), not a cue phrase
            1.0 if tset & {"i", "i'll", "i'm", "i'd", "me", "my", "मैं"} else 0.0,
            1.0 if tset & {"you", "you'll", "your", "aap", "tum", "तुम", "आप"} else 0.0,
            1.0 if tset & {"we", "we'll", "we're", "us", "our", "hum", "हम"} else 0.0,
        ])
    return np.asarray(rows, dtype=np.float32)


def context_features(emb: np.ndarray, texts: list[str], speakers: list[str], use_position: bool = True) -> np.ndarray:
    """[own embedding, mean of 2 previous, mean of 2 next, structural]."""
    n, d = emb.shape
    prev = np.zeros_like(emb)
    nxt = np.zeros_like(emb)
    for i in range(n):
        if i > 0:
            prev[i] = emb[max(0, i - 2): i].mean(axis=0)
        if i < n - 1:
            nxt[i] = emb[i + 1: i + 3].mean(axis=0)
    return np.hstack([emb, prev, nxt, structural_features(texts, speakers, use_position)]).astype(np.float32)


def pair_features(emb: np.ndarray, response: int, target: int, speakers: list[str], texts: list[str]) -> np.ndarray:
    """Features for 'how does utterance `response` react to utterance `target`'."""
    struct = structural_features([texts[response]], [speakers[response]])[0]
    return np.hstack([emb[response], emb[target], emb[response] * emb[target], struct,
                      [1.0 if speakers[response] != speakers[target] else 0.0,
                       min(5, response - target) / 5.0]]).astype(np.float32)


# ---------------------------------------------------------------------------
# Linear heads stored as numpy arrays
# ---------------------------------------------------------------------------

class LinearHead:
    def __init__(self, coef: np.ndarray, intercept: np.ndarray, classes: list[str], mean: np.ndarray, scale: np.ndarray):
        self.coef, self.intercept, self.classes = coef, intercept, classes
        self.mean, self.scale = mean, scale

    def proba(self, x: np.ndarray) -> np.ndarray:
        z = ((x - self.mean) / self.scale) @ self.coef.T + self.intercept
        if self.coef.shape[0] == 1:  # binary: column for class 1
            p = 1.0 / (1.0 + np.exp(-z[:, 0]))
            return np.stack([1 - p, p], axis=1)
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)


class DialogueModel:
    def __init__(self, path: Path = MODEL_PATH):
        data = np.load(path, allow_pickle=False)
        self.meta = json.loads(str(data["meta"]))
        self.heads: dict[str, LinearHead] = {}
        for name in self.meta["heads"]:
            self.heads[name] = LinearHead(
                data[f"{name}__coef"], data[f"{name}__intercept"], self.meta["classes"][name],
                data[f"{name}__mean"], data[f"{name}__scale"],
            )
        self.thresholds: dict[str, float] = self.meta["thresholds"]

    @property
    def version(self) -> str:
        return self.meta["version"]


@lru_cache(maxsize=1)
def load_model(path: str = str(MODEL_PATH)) -> DialogueModel:
    return DialogueModel(Path(path))


def model_available() -> bool:
    return MODEL_PATH.exists()


def embed(texts: list[str]) -> np.ndarray:
    from app.core.model_registry import get_sentence_transformer
    from app.services.verification.retrieval_index import EMBED_MODEL_NAME

    model = get_sentence_transformer(EMBED_MODEL_NAME)
    return np.asarray(model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False),
                      dtype=np.float32)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def score_utterances(model: DialogueModel, emb: np.ndarray, texts: list[str], speakers: list[str]) -> dict[str, np.ndarray]:
    x = context_features(emb, texts, speakers, model.meta.get("features", {}).get("use_position", True))
    return {name: model.heads[name].proba(x) for name in ("speech_act", "decision", "action")}


def response_polarity(model: DialogueModel, emb: np.ndarray, texts: list[str], speakers: list[str],
                      target: int, window: int, cache: Optional[dict] = None) -> tuple[Optional[str], float, Optional[int]]:
    """
    Strongest reply to `target` among the next `window` utterances by other
    speakers. The polarity head includes a NONE class ("not a reply to this"),
    so ordinary backchannels and unrelated turns do not count as agreement.
    """
    head = model.heads["polarity"]
    best: tuple[Optional[str], float, Optional[int]] = (None, 0.0, None)
    for r in range(target + 1, min(len(texts), target + 1 + window)):
        if speakers[r] == speakers[target]:
            continue
        key = (r, target)
        if cache is not None and key in cache:
            p = cache[key]
        else:
            p = head.proba(pair_features(emb, r, target, speakers, texts)[None, :])[0]
            if cache is not None:
                cache[key] = p
        cls = head.classes[int(p.argmax())]
        if cls == "NONE":
            continue
        if p.max() > best[1]:
            best = (cls, float(p.max()), r)
    return best


def decode(model: DialogueModel, utterances: list[Any], emb: np.ndarray,
           scores: Optional[dict] = None, pair_cache: Optional[dict] = None) -> list[dict[str, Any]]:
    """Turn per-utterance scores into claim candidates (no window/meeting fields yet)."""
    from app.services.intelligence.action_item_extractor import ActionItemExtractor

    th = model.thresholds
    texts = [u.text for u in utterances]
    speakers = [u.speaker_id for u in utterances]
    if scores is None:
        scores = score_utterances(model, emb, texts, speakers)
    p_dec = scores["decision"][:, 1]
    p_act = scores["action"][:, 1]
    sa_classes = model.heads["speech_act"].classes
    # Only its owner/deadline helpers are used; its constructor creates an
    # output folder, so point that at the temp dir rather than the source tree.
    helper = ActionItemExtractor(output_dir=tempfile.gettempdir())

    offer_threshold = th.get("offer")
    p_offer = scores["speech_act"][:, sa_classes.index("offer")] if "offer" in sa_classes else np.zeros(len(texts))
    picked = []
    for i in range(len(utterances)):
        if len(_tokens(texts[i])) < th.get("min_tokens", 3):
            continue
        kind = None
        # A clear offer ("I can deploy a workaround") is an action candidate even
        # where the AMI-trained action head is silent; the speech-act head was
        # trained on generic dialogue-act labels and transfers across meetings.
        if offer_threshold is not None and p_offer[i] >= offer_threshold:
            kind = "action"
        elif p_dec[i] >= th["decision"] and p_dec[i] / th["decision"] >= p_act[i] / th["action"]:
            kind = "decision"
        elif p_act[i] >= th["action"]:
            kind = "action"
        if kind:
            picked.append((i, kind))

    # merge adjacent candidates of the same type into one claim
    groups: list[list[tuple[int, str]]] = []
    for i, kind in picked:
        if groups and groups[-1][-1][1] == kind and i - groups[-1][-1][0] <= 1:
            groups[-1].append((i, kind))
        else:
            groups.append([(i, kind)])

    candidates = []
    for group in groups:
        idx = [i for i, _ in group][:3]
        kind = group[0][1]
        anchor = max(idx, key=lambda i: p_dec[i] if kind == "decision" else p_act[i])
        last = idx[-1]
        polarity, pol_conf, responder = response_polarity(
            model, emb, texts, speakers, last, th.get("response_window", 3), pair_cache
        )
        accepted = polarity == "POS" and pol_conf >= th["pos"]
        objected = polarity == "NEG" and pol_conf >= th["neg"]
        partial = polarity in ("PART", "UNC") and pol_conf >= th["part"]
        evidence = [utterances[i].utt_id for i in idx]
        if responder is not None and (accepted or objected or partial):
            evidence.append(utterances[responder].utt_id)
        sa = sa_classes[int(scores["speech_act"][anchor].argmax())]
        text = " ".join(texts[i] for i in idx)
        base = {
            "claim_type": kind,
            "text": text[:300],
            "evidence": evidence,
            "_scores": {
                "decision": round(float(p_dec[anchor]), 4), "action": round(float(p_act[anchor]), 4),
                "speech_act": sa, "response": polarity, "response_conf": round(pol_conf, 4),
            },
        }
        if kind == "decision":
            state = "accepted" if accepted else "rejected" if objected else "conditional" if partial else "proposed"
            base.update({
                "state": state, "conditional": state == "conditional", "condition_text": None,
                "proposer": None,
                "uncertainty": "low" if accepted else "medium" if partial else "high",
            })
        else:
            owner, owner_kind, _ = helper.detect_owner(texts[anchor], speakers[anchor])
            owner_type = {
                "speaker_commitment": "explicit_first_person", "explicit_name": "explicit_third_person",
                "direct_assignment": "explicit_third_person", "group_commitment": "group",
            }.get(owner_kind, "unassigned")
            if owner in ("GROUP", "UNKNOWN"):
                owner = None if owner == "UNKNOWN" else "group"
            base.update({
                "owner": owner if owner_type != "unassigned" else None,
                "owner_type": owner_type,
                "deadline_text": helper.extract_deadline(text),
                "commitment_type": "firm" if accepted else "tentative",
            })
        candidates.append(base)
    return candidates


def merge_candidates(rules: list[dict[str, Any]], learned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Hybrid extraction. Roles are deliberately asymmetric:

    - every rules candidate is kept;
    - the learned model ADDS candidates the rules missed, but those are never
      final: decisions enter as "proposed" (or the model's conditional/rejected),
      actions as "tentative". They are surfaced for review, never confirmed.
      (On AMI dev, 73% of the learned model's own "accepted" states were wrong.)
    - where both describe the same evidence, the learned model may only make the
      state MORE cautious (conditional/rejected over "accepted"), never more final.

    Result: hybrid never produces more confirmation-state claims than the rules
    alone, while recall can only go up.
    """
    out = [dict(c) for c in rules]
    for cand in learned:
        twin = next(
            (r for r in out if r["claim_type"] == cand["claim_type"]
             and set(r.get("evidence") or []) & set(cand["evidence"])),
            None,
        )
        if twin is None:
            added = dict(cand)
            if added["claim_type"] == "decision" and added.get("state") == "accepted":
                added["state"] = "proposed"
            if added["claim_type"] == "action":
                added["commitment_type"] = "tentative"
            out.append(added)
            continue
        if cand["claim_type"] == "decision":
            if cand["state"] in ("conditional", "rejected") and twin.get("state") == "accepted":
                twin["state"] = cand["state"]
                twin["conditional"] = cand["state"] == "conditional"
        else:
            if not twin.get("owner") and cand.get("owner"):
                twin["owner"], twin["owner_type"] = cand["owner"], cand["owner_type"]
            if not twin.get("deadline_text") and cand.get("deadline_text"):
                twin["deadline_text"] = cand["deadline_text"]
    return out


def extract_with_dialogue_model(meeting_id: str, windows: list[Any]) -> list[dict[str, Any]]:
    """Production entry point: candidates in the shape validate_candidates() expects."""
    from app.core import db

    model = load_model()
    utterances = db.get_utterances(meeting_id)
    if not utterances:
        return []
    emb = embed([u.text for u in utterances])
    candidates = decode(model, utterances, emb)
    out = []
    for cand in candidates:
        window = next((w for w in windows if set(cand["evidence"]) <= set(w.utt_ids)), None)
        if window is None:  # evidence spans a window boundary: keep the anchor window only
            window = next((w for w in windows if cand["evidence"][0] in w.utt_ids), windows[0])
            cand["evidence"] = [e for e in cand["evidence"] if e in set(window.utt_ids)]
        cand.pop("_scores", None)
        cand.update({"meeting_id": meeting_id, "window_id": window.window_id})
        out.append(cand)
    return out
