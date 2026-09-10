import pytest
from datetime import datetime
from pydantic import ValidationError

from app.schemas import (
    Utterance,
    ClaimBase,
    Decision,
    ActionItem,
    VerificationResult,
    JobStatus,
)


def test_claim_empty_evidence_rejected():
    """Prove a claim with evidence=[] is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        ClaimBase(
            claim_id="c0001",
            meeting_id="m001",
            text="Test claim text",
            evidence=[],
        )
    assert "at least 1 item" in str(exc_info.value).lower() or "min_length" in str(exc_info.value).lower() or "too_short" in str(exc_info.value)


def test_invalid_utt_id_format_rejected():
    """Prove an invalid utt_id format is rejected."""
    # In Utterance
    with pytest.raises(ValidationError) as exc_info:
        Utterance(
            meeting_id="m001",
            utt_id="invalid_id",
            start=0.0,
            end=1.5,
            speaker_id="spk_0",
            text="Hello world",
            asr_conf=0.95,
            spk_conf=0.90,
        )
    assert "must match pattern" in str(exc_info.value)

    # In ClaimBase evidence
    with pytest.raises(ValidationError) as exc_info2:
        ClaimBase(
            claim_id="c0001",
            meeting_id="m001",
            text="Decision statement",
            evidence=["u123"],  # needs 5 digits
        )
    assert "must match pattern" in str(exc_info2.value)


def test_utterance_end_le_start_rejected():
    """Prove end <= start is rejected."""
    # end < start
    with pytest.raises(ValidationError) as exc_info:
        Utterance(
            meeting_id="m001",
            utt_id="u00001",
            start=5.0,
            end=4.0,
            speaker_id="spk_0",
            text="Hello",
            asr_conf=0.9,
            spk_conf=0.9,
        )
    assert "must be strictly greater than start" in str(exc_info.value)

    # end == start
    with pytest.raises(ValidationError) as exc_info2:
        Utterance(
            meeting_id="m001",
            utt_id="u00002",
            start=5.0,
            end=5.0,
            speaker_id="spk_0",
            text="Hello",
            asr_conf=0.9,
            spk_conf=0.9,
        )
    assert "must be strictly greater than start" in str(exc_info2.value)


def test_valid_models():
    """Prove valid models construct and validate cleanly."""
    utt = Utterance(
        meeting_id="m001",
        utt_id="u00000",
        start=0.0,
        end=2.5,
        speaker_id="SPEAKER_00",
        speaker_name="Alice",
        text="Let's proceed with PostgreSQL.",
        lang_tags=["en"],
        script="latin",
        asr_conf=0.98,
        spk_conf=0.92,
        overlap=False,
        snr=24.5,
        topic_id="t001",
    )
    assert utt.utt_id == "u00000"
    assert utt.end > utt.start

    dec = Decision(
        claim_id="d0001",
        meeting_id="m001",
        text="We decided to use PostgreSQL.",
        evidence=["u00000"],
        state="accepted",
        proposer="Alice",
        conditional=False,
        uncertainty="low",
    )
    assert dec.claim_type == "decision"
    assert dec.state == "accepted"

    act = ActionItem(
        claim_id="a0001",
        meeting_id="m001",
        text="Bob will configure the database.",
        evidence=["u00000"],
        owner="Bob",
        owner_type="explicit_third_person",
        deadline_text="by Friday",
        deadline_type="relative",
        deadline_resolved="2026-09-12",
        commitment_type="firm",
    )
    assert act.claim_type == "action"
    assert act.owner == "Bob"

    ver = VerificationResult(
        claim_id="d0001",
        status="verified",
        entail_score=0.95,
        contradict_score=0.02,
        fused_score=0.94,
        best_span="Let's proceed with PostgreSQL",
        signals={"lexical": 0.9},
    )
    assert ver.status == "verified"

    job = JobStatus(
        meeting_id="m001",
        stage="transcription",
        progress=50.0,
        state="running",
        error=None,
        updated_at=datetime.utcnow(),
    )
    assert job.state == "running"
