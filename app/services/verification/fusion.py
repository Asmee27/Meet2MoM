"""
Trust-status fusion v1 (Phase 1).

Combines the deterministic verifier, the semantic (retrieval + NLI) verifier,
claim semantics (decision state, owner, deadline) and audio reliability into
one of four statuses, each with a list of human-readable reasons:

    verified | verified_with_warning | review | rejected

These are transparent, PROVISIONAL rules. The thresholds below are not
calibrated probabilities; calibration against labelled data is Phase 2.
Missing inputs are treated as unknown (a warning), never as perfect confidence.
When evidence is insufficient the result is "review", not a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

FUSION_CONFIG: dict[str, Any] = {
    "version": "fusion-v1-provisional",
    # NLI contradiction on the cited span strong enough to reject the claim.
    "contradiction_reject": 0.80,
    # NLI contradiction anywhere (cited or retrieved) worth a human look.
    "contradiction_review": 0.50,
    # Entailment from the cited utterances alone (no context) below which the
    # citation is considered incomplete even if context makes it supported.
    "cited_strict_min": 0.50,
    # Per-hypothesis entailment below which an owner/deadline is "not supported".
    "owner_support_min": 0.50,
    "deadline_support_min": 0.50,
    # Audio reliability thresholds (same scale as the stored utterance fields).
    "low_asr_conf": 0.50,
    "low_spk_conf": 0.60,
    "high_no_speech_prob": 0.60,
}

VERIFIED = "verified"
VERIFIED_WITH_WARNING = "verified_with_warning"
REVIEW = "review"
REJECTED = "rejected"
FINAL_LABELS = (VERIFIED, VERIFIED_WITH_WARNING, REVIEW, REJECTED)
EXPORTABLE_LABELS = (VERIFIED, VERIFIED_WITH_WARNING)

_SEVERITY_ORDER = {"info": 0, "warning": 1, "review": 2, "reject": 3}
_UNKNOWN_SPEAKERS = {"", "UNKNOWN", "SPEAKER_UNKNOWN"}


@dataclass
class Reason:
    code: str
    severity: str  # info | warning | review | reject
    message: str
    utt_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "utt_ids": list(self.utt_ids),
        }


def _fmt(value: Optional[float]) -> str:
    return "unknown" if value is None else f"{value:.2f}"


def _reliability_reasons(cited: Iterable[Any], cfg: dict[str, Any]) -> list[Reason]:
    cited = list(cited)
    reasons: list[Reason] = []
    if not cited:
        return reasons

    asr = [u.asr_conf for u in cited]
    if any(value is None for value in asr):
        reasons.append(Reason(
            "asr_confidence_unknown", "warning",
            "ASR confidence is unknown for the cited speech (e.g. imported transcript); it is not assumed to be reliable.",
            [u.utt_id for u in cited if u.asr_conf is None],
        ))
    known_asr = [value for value in asr if value is not None]
    if known_asr and min(known_asr) < cfg["low_asr_conf"]:
        reasons.append(Reason(
            "low_asr_confidence", "warning",
            f"Low ASR confidence on the cited speech (min {min(known_asr):.2f}).",
            [u.utt_id for u in cited if u.asr_conf is not None and u.asr_conf < cfg["low_asr_conf"]],
        ))

    spk = [u.spk_conf for u in cited]
    if any(value is None for value in spk):
        reasons.append(Reason(
            "speaker_confidence_unknown", "warning",
            "Speaker-attribution confidence is unknown for the cited speech.",
            [u.utt_id for u in cited if u.spk_conf is None],
        ))
    known_spk = [value for value in spk if value is not None]
    if known_spk and min(known_spk) < cfg["low_spk_conf"]:
        reasons.append(Reason(
            "low_speaker_confidence", "warning",
            f"Uncertain speaker attribution on the cited speech (min {min(known_spk):.2f}).",
            [u.utt_id for u in cited if u.spk_conf is not None and u.spk_conf < cfg["low_spk_conf"]],
        ))

    overlapping = [u.utt_id for u in cited if u.overlap]
    if overlapping:
        reasons.append(Reason(
            "overlapping_speech", "warning",
            "The cited speech overlaps with another speaker (possible cross-talk).",
            overlapping,
        ))

    non_speech = [
        u.utt_id for u in cited
        if u.no_speech_prob is not None and u.no_speech_prob > cfg["high_no_speech_prob"]
    ]
    if non_speech:
        reasons.append(Reason(
            "possible_non_speech", "warning",
            "The ASR model flagged the cited audio as possibly not speech.",
            non_speech,
        ))

    unknown = [u.utt_id for u in cited if str(u.speaker_id or "").upper() in _UNKNOWN_SPEAKERS]
    if unknown:
        reasons.append(Reason(
            "unknown_speaker", "warning", "The cited speech has no identified speaker.", unknown,
        ))
    return reasons


def _semantic_reasons(semantic: dict[str, Any], cited_ids: list[str], cfg: dict[str, Any]) -> list[Reason]:
    label = semantic.get("label")
    reasons: list[Reason] = []
    if label in ("unavailable", "error"):
        reasons.append(Reason(
            "semantic_verifier_unavailable", "review",
            "Semantic verification could not run: "
            f"{semantic.get('reason') or 'model unavailable'}. Meaning was not checked, so the claim needs review.",
        ))
        return reasons
    if label == "no_cited_evidence":
        reasons.append(Reason(
            "cited_evidence_missing", "reject",
            "The claim's cited utterances could not be resolved in the transcript.",
        ))
        return reasons

    e_cited = semantic.get("e_cited")
    x_cited = semantic.get("x_cited")
    if label == "contradicted":
        reasons.append(Reason(
            "cited_evidence_contradicts", "reject",
            f"The cited utterances contradict the claim (contradiction {_fmt(x_cited)} vs entailment {_fmt(e_cited)}).",
            cited_ids,
        ))
    elif label == "supported":
        reasons.append(Reason(
            "cited_evidence_entails", "info",
            f"The cited utterances entail the claim (entailment {_fmt(e_cited)}, margin over background "
            f"{_fmt(semantic.get('e_cited_margin'))}).",
            cited_ids,
        ))
    elif label == "supported_with_context":
        reasons.append(Reason(
            "citation_incomplete", "review",
            "The cited utterances support the claim only together with neighbouring utterances "
            f"(cited alone {_fmt(semantic.get('e_cited_strict'))}, with context {_fmt(e_cited)}). "
            "The citation is incomplete; a corrected evidence span is suggested.",
            list(semantic.get("proposed_utt_ids") or []),
        ))
    elif label == "supported_elsewhere":
        reasons.append(Reason(
            "possible_miscitation", "review",
            "The cited utterances do not support the claim, but other utterances do "
            f"(best support {_fmt(semantic.get('best_support'))} vs cited {_fmt(e_cited)}). "
            "A corrected evidence span is suggested.",
            list(semantic.get("proposed_utt_ids") or []),
        ))
    else:  # unsupported
        reasons.append(Reason(
            "not_entailed", "review",
            f"Neither the cited nor the retrieved utterances clearly entail the claim (cited entailment {_fmt(e_cited)}, "
            f"best {_fmt(semantic.get('best_support'))}).",
            cited_ids,
        ))

    x_core = semantic.get("x_core")
    x_ids = list(semantic.get("x_core_utt_ids") or [])
    if (
        label != "contradicted"
        and x_core is not None
        and x_core >= cfg["contradiction_review"]
        and x_ids
    ):
        elsewhere = not set(x_ids) <= set(cited_ids)
        reasons.append(Reason(
            "contradicting_evidence_elsewhere" if elsewhere else "cited_evidence_partially_contradicts",
            "review",
            (
                f"Other utterances appear to contradict the claim (contradiction {x_core:.2f}); "
                "this may be a reversal or a rejection and must be checked. It is not assumed to supersede the claim."
                if elsewhere
                else f"Part of the cited span appears to contradict the claim (contradiction {x_core:.2f})."
            ),
            x_ids,
        ))
    return reasons


def _deterministic_reasons(deterministic: Optional[dict[str, Any]]) -> list[Reason]:
    if not deterministic:
        return [Reason("deterministic_unavailable", "warning", "The deterministic evidence check did not run.")]
    label = deterministic.get("label")
    if label in ("verified", "verified_with_warning"):
        if deterministic.get("matched_utt_id") and not deterministic.get("matches_cited", True):
            return [Reason(
                "deterministic_match_elsewhere", "warning",
                "The text-matching check found its best match outside the cited utterances.",
                [deterministic["matched_utt_id"]],
            )]
        return []
    if label == "review":
        return [Reason(
            "deterministic_match_weak", "warning",
            "The text-matching check found only a partial match for the claim wording.",
        )]
    if label == "rejected":
        return [Reason(
            "deterministic_no_match", "warning",
            "The text-matching check found no close transcript match for the claim wording "
            "(it can miss paraphrases; the semantic check decides support).",
        )]
    return [Reason("deterministic_unavailable", "warning", f"The deterministic check returned '{label}'.")]


def _claim_reasons(claim: dict[str, Any], semantic: dict[str, Any], cited: list[Any], cfg: dict[str, Any]) -> list[Reason]:
    reasons: list[Reason] = []
    payload = claim.get("payload") or {}
    if claim.get("claim_type") == "decision":
        state = payload.get("state")
        if state == "proposed":
            reasons.append(Reason(
                "proposal_without_agreement", "review",
                "This was proposed, but no agreement was detected. A proposal is not an approved decision.",
            ))
        elif state == "conditional" or payload.get("conditional"):
            reasons.append(Reason(
                "conditional_decision", "review",
                "This decision depends on a condition"
                + (f" ({payload['condition_text']})" if payload.get("condition_text") else "")
                + "; it is not final until the condition is confirmed.",
            ))
        elif state == "deferred":
            reasons.append(Reason("deferred_decision", "review", "The decision was deferred, not taken."))
        elif state == "rejected":
            reasons.append(Reason(
                "rejected_proposal", "review",
                "Recorded as a rejected proposal; it is not a decision to act on.",
            ))
        return reasons

    # Action items
    commitment = payload.get("commitment_type", "firm")
    if commitment in ("hypothetical", "past_action"):
        reasons.append(Reason(
            f"{commitment}_commitment", "review",
            "This reads as a hypothetical statement or a past action, not a commitment."
            if commitment == "hypothetical"
            else "This describes something already done, not a new task.",
        ))
    elif commitment == "tentative":
        reasons.append(Reason(
            "tentative_commitment", "review",
            "The commitment is tentative or conditional; it is not a firm task.",
        ))
    elif commitment == "assigned_to_absent":
        reasons.append(Reason(
            "assigned_to_absent", "warning", "The task was assigned to someone who may not be present.",
        ))

    owner = payload.get("owner")
    owner_type = payload.get("owner_type")
    per_hyp = semantic.get("per_hypothesis") or {}
    if not owner or owner_type == "unassigned":
        reasons.append(Reason("no_owner", "warning", "No owner was assigned to this task."))
    elif owner_type == "group":
        reasons.append(Reason("group_owner", "warning", f"The task is owned by a group ({owner}), not a person."))
    elif owner_type == "explicit_first_person":
        known = [u.spk_conf for u in cited if u.spk_conf is not None]
        if known and min(known) < cfg["low_spk_conf"]:
            reasons.append(Reason(
                "owner_from_uncertain_speaker", "review",
                "The owner comes from the speaker label, but speaker attribution is uncertain.",
                [u.utt_id for u in cited],
            ))
    if owner and owner_type in ("explicit_third_person", "contextual") and "owner" in per_hyp:
        if per_hyp["owner"] < cfg["owner_support_min"]:
            reasons.append(Reason(
                "owner_not_supported", "review",
                f"The evidence does not clearly support {owner} as the owner (entailment {per_hyp['owner']:.2f}).",
            ))

    deadline_text = payload.get("deadline_text")
    names = [owner] + [getattr(u, a, None) for u in cited for a in ("speaker_name", "speaker_id")]
    trailing = unrecognized_deadline_phrase(payload.get("text") or "", names) if not deadline_text else None
    if trailing:
        # The task wording ends like a deadline ("... by Twitter") that is not
        # a date. It is not treated as one; a reviewer checks the audio.
        reasons.append(Reason(
            "deadline_unrecognized", "warning",
            f"The task ends with \"{trailing}\", which is not a recognisable date or deadline, so the deadline is "
            "unknown. If the words were misheard, correct the transcript against the audio.",
            [u.utt_id for u in cited],
        ))
    elif not deadline_text:
        reasons.append(Reason("no_deadline", "warning", "No deadline was stated."))
    else:
        if not payload.get("deadline_resolved"):
            reasons.append(Reason(
                "deadline_ambiguous", "warning",
                f"The deadline \"{deadline_text}\" could not be resolved to a date.",
            ))
        if "deadline" in per_hyp and per_hyp["deadline"] < cfg["deadline_support_min"]:
            reasons.append(Reason(
                "deadline_not_supported", "warning",
                f"The evidence does not clearly support the deadline \"{deadline_text}\" (entailment {per_hyp['deadline']:.2f}).",
            ))
    return reasons


# "by <word>" at the end of a task that names a means, not a time.
_MEANS_WORDS = {
    "email", "mail", "e-mail", "phone", "post", "hand", "courier", "chat", "message", "slack",
    "teams", "whatsapp", "text", "call", "me", "you", "us", "them", "him", "her", "myself", "yourself",
}
_TRAILING_DEADLINE = re.compile(
    r"\b(by|before|until|till)\s+((?:the\s+)?[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*)?)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def unrecognized_deadline_phrase(text: str, known_names: Iterable[str] = ()) -> Optional[str]:
    """
    The trailing "by X" phrase of a task when X is not a recognisable deadline
    (e.g. an ASR mishearing such as "by Twitter"), else None. Recognised dates,
    weekdays, relative and event-anchored deadlines, means ("by email") and
    people known in the meeting ("approved by Priya") return None.
    """
    from datetime import date

    from app.services.intelligence.deadline_resolver import resolve_deadline

    match = _TRAILING_DEADLINE.search(str(text or ""))
    if not match:
        return None
    words = match.group(2).lower().split()
    names = {n.lower() for name in known_names if name for n in str(name).split()}
    if words and (words[-1] in _MEANS_WORDS or words[0] in _MEANS_WORDS or words[-1] in names):
        return None
    phrase = f"{match.group(1)} {match.group(2)}"
    _, kind, _ = resolve_deadline(phrase, date.today())
    return phrase if kind == "none" else None


def fuse(
    claim: dict[str, Any],
    deterministic: Optional[dict[str, Any]],
    semantic: dict[str, Any],
    cited_utterances: list[Any],
    correction: Optional[dict[str, Any]] = None,
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return {"label", "reasons", "config_version"} for one claim."""
    cfg = cfg or FUSION_CONFIG
    cited_ids = list(claim.get("evidence") or [])
    reasons: list[Reason] = []

    if not cited_ids or not cited_utterances:
        reasons.append(Reason(
            "cited_evidence_missing", "reject", "The claim has no resolvable transcript evidence.",
        ))

    reasons.extend(_semantic_reasons(semantic, cited_ids, cfg))
    reasons.extend(_deterministic_reasons(deterministic))
    reasons.extend(_claim_reasons(claim, semantic, cited_utterances, cfg))
    reasons.extend(_reliability_reasons(cited_utterances, cfg))

    if correction and correction.get("action") == "rejected":
        reasons.append(Reason(
            "suggestion_rejected_by_reviewer", "info",
            "A reviewer rejected the suggested evidence span.",
            list(correction.get("proposed_utt_ids") or []),
        ))
    elif correction and correction.get("action") == "accepted":
        reasons.append(Reason(
            "evidence_corrected_by_reviewer", "info",
            "A reviewer replaced the cited evidence with the suggested span.",
            list(correction.get("proposed_utt_ids") or []),
        ))

    # Deduplicate by code, keeping the most severe entry.
    unique: dict[str, Reason] = {}
    for reason in reasons:
        existing = unique.get(reason.code)
        if existing is None or _SEVERITY_ORDER[reason.severity] > _SEVERITY_ORDER[existing.severity]:
            unique[reason.code] = reason
    reasons = list(unique.values())

    worst = max((_SEVERITY_ORDER[r.severity] for r in reasons), default=0)
    if worst >= _SEVERITY_ORDER["reject"]:
        label = REJECTED
    elif worst >= _SEVERITY_ORDER["review"]:
        label = REVIEW
    elif semantic.get("label") != "supported":
        # Never mark a claim verified without positive semantic support.
        label = REVIEW
        reasons.append(Reason(
            "no_positive_support", "review", "No positive semantic support was established for this claim.",
        ))
    elif worst >= _SEVERITY_ORDER["warning"]:
        label = VERIFIED_WITH_WARNING
    else:
        label = VERIFIED

    reasons.sort(key=lambda r: -_SEVERITY_ORDER[r.severity])
    return {
        "label": label,
        "reasons": [r.as_dict() for r in reasons],
        "config_version": cfg["version"],
    }
