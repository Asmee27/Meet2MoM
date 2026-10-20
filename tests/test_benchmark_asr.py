"""
tests/test_benchmark_asr.py
===========================
Unit tests for scripts/benchmark_asr.py:
- WER (Word Error Rate) calculation logic
- Segment confidence analysis
- CPU info retrieval
- Benchmark report structure and calculations
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.benchmark_asr import (
    BASELINE_ASR_TIME,
    BASELINE_RTF,
    analyze_segment_confidence,
    compute_wer,
    get_cpu_info,
    generate_reports,
)


def test_compute_wer_identical_strings():
    text = "Paul is the Human Resources Manager at Quartz Power Group."
    result = compute_wer(text, text)
    assert result["wer"] == 0.0
    assert result["edit_distance"] == 0
    assert result["reference_words"] == 10
    assert result["hypothesis_words"] == 10


def test_compute_wer_with_known_substitutions_and_deletions():
    ref = "the quick brown fox jumps over the lazy dog"
    hyp = "the fast brown fox jumps over lazy dog"  # 1 substitution (fast/quick), 1 deletion ("the")
    result = compute_wer(ref, hyp)
    assert result["reference_words"] == 9
    assert result["hypothesis_words"] == 8
    assert result["edit_distance"] == 2
    assert result["wer"] == round(2 / 9, 4)


def test_compute_wer_empty_strings():
    assert compute_wer("", "")["wer"] == 0.0
    assert compute_wer("hello world", "")["wer"] == 1.0
    assert compute_wer("", "hello world")["wer"] == 1.0


def test_cpu_info_returns_expected_structure():
    info = get_cpu_info()
    assert "model" in info
    assert "machine" in info
    assert "logical_cores" in info
    assert isinstance(info["logical_cores"], int)
    assert info["logical_cores"] >= 1


def test_analyze_segment_confidence():
    # Normal segment
    normal_seg = MagicMock()
    normal_seg.avg_logprob = -0.2
    normal_seg.no_speech_prob = 0.1
    normal_seg.compression_ratio = 1.2
    assert analyze_segment_confidence(normal_seg) is False

    # Low log probability
    low_logprob_seg = MagicMock()
    low_logprob_seg.avg_logprob = -1.5
    low_logprob_seg.no_speech_prob = 0.1
    low_logprob_seg.compression_ratio = 1.2
    assert analyze_segment_confidence(low_logprob_seg) is True

    # High no speech probability
    high_no_speech_seg = MagicMock()
    high_no_speech_seg.avg_logprob = -0.2
    high_no_speech_seg.no_speech_prob = 0.8
    high_no_speech_seg.compression_ratio = 1.2
    assert analyze_segment_confidence(high_no_speech_seg) is True


def test_generate_reports_creates_valid_json_and_markdown(tmp_path: Path):
    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"

    mock_benchmark_data = {
        "timestamp": "2026-10-18 12:00:00 UTC",
        "input_file": "input/test_meeting.mp4",
        "cpu_info": {
            "model": "Mock Processor",
            "machine": "x86_64",
            "logical_cores": 8,
        },
        "os_info": "Windows 11",
        "asr_benchmark": {
            "model_size": "small",
            "compute_type": "int8",
            "device": "cpu",
            "beam_size": 5,
            "vad_filter": True,
            "model_load_time_seconds": 2.5,
            "audio_duration_seconds": 248.13,
            "total_runs": 3,
            "cold_run": {
                "run_index": 1,
                "is_cold": True,
                "wall_time_seconds": 75.0,
                "rtf": 0.302,
                "segment_count": 35,
                "low_confidence_segments": 0,
            },
            "warm_runs": [
                {
                    "run_index": 2,
                    "is_cold": False,
                    "wall_time_seconds": 68.0,
                    "rtf": 0.274,
                    "segment_count": 35,
                    "low_confidence_segments": 0,
                },
                {
                    "run_index": 3,
                    "is_cold": False,
                    "wall_time_seconds": 70.0,
                    "rtf": 0.282,
                    "segment_count": 35,
                    "low_confidence_segments": 0,
                },
            ],
            "warm_mean_time_seconds": 69.0,
            "warm_min_time_seconds": 68.0,
            "warm_mean_rtf": 0.278,
            "warm_min_rtf": 0.274,
            "runs_detail": [
                {
                    "run_index": 1,
                    "is_cold": True,
                    "wall_time_seconds": 75.0,
                    "rtf": 0.302,
                    "segment_count": 35,
                    "low_confidence_segments": 0,
                },
                {
                    "run_index": 2,
                    "is_cold": False,
                    "wall_time_seconds": 68.0,
                    "rtf": 0.274,
                    "segment_count": 35,
                    "low_confidence_segments": 0,
                },
                {
                    "run_index": 3,
                    "is_cold": False,
                    "wall_time_seconds": 70.0,
                    "rtf": 0.282,
                    "segment_count": 35,
                    "low_confidence_segments": 0,
                },
            ],
        },
        "quality_comparison": {
            "available": True,
            "golden_file": "demo_01",
            "golden_segment_count": 39,
            "hypothesis_segment_count": 35,
            "golden_low_confidence_count": 0,
            "hypothesis_low_confidence_count": 0,
            "wer_stats": {
                "reference_words": 566,
                "hypothesis_words": 565,
                "edit_distance": 7,
                "wer": 0.0124,
            },
        },
        "e2e_pipeline": {
            "meeting_id": "test_e2e",
            "wall_time_seconds": 503.51,
            "success": True,
            "error": None,
        },
        "baseline_comparison": {
            "baseline_asr_time_seconds": BASELINE_ASR_TIME,
            "baseline_audio_duration_seconds": 248.13,
            "baseline_rtf": BASELINE_RTF,
            "speedup_vs_baseline_min": 6.28,
            "speedup_vs_baseline_mean": 6.19,
        },
    }

    generate_reports(mock_benchmark_data, json_path, md_path)

    assert json_path.exists()
    assert md_path.exists()

    with open(json_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["asr_benchmark"]["warm_min_rtf"] == 0.274

    md_text = md_path.read_text(encoding="utf-8")
    assert "Meet2MoM — Phase 3 Real-Time Factor (RTF) Benchmark Report" in md_text
    assert "PASSED (Phase 3 Gate 3 Satisfied)" in md_text
    assert "0.274" in md_text
