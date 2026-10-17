"""
Regression test for Phase 0 P1-1: pipeline output crashed with
UnicodeEncodeError when stdout was a non-UTF-8 (cp1252) pipe on Windows.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PIPELINE_STYLE_OUTPUT = (
    "print('\\u2713 Recording hash: abc')\n"
    "print('\\u26a0 Cache save failed')\n"
    "import logging\n"
    "logging.basicConfig(format='%(message)s')\n"
    "logging.getLogger('t').warning('\\u2717 MEET2MOM PIPELINE FAILED')\n"
)


def _run(code: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONPATH": str(PROJECT_ROOT)}
    env.pop("PYTHONUTF8", None)
    return subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", code],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        timeout=120,
    )


def test_cp1252_pipe_reproduces_original_crash():
    # Control: without the fix the same output raises UnicodeEncodeError.
    result = _run(PIPELINE_STYLE_OUTPUT)
    assert result.returncode != 0
    assert b"UnicodeEncodeError" in result.stderr


def test_unicode_safe_stdio_prevents_crash_on_cp1252_pipe():
    code = (
        "from app.core.console import make_stdio_unicode_safe\n"
        "make_stdio_unicode_safe()\n" + PIPELINE_STYLE_OUTPUT
    )
    result = _run(code)
    assert result.returncode == 0, result.stderr.decode("cp1252", "replace")
    assert b"\\u2713 Recording hash: abc" in result.stdout
    assert b"\\u26a0 Cache save failed" in result.stdout
    assert b"\\u2717 MEET2MOM PIPELINE FAILED" in result.stderr


def test_utf8_streams_are_left_unchanged():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(PROJECT_ROOT)}
    code = (
        "import sys\n"
        "from app.core.console import make_stdio_unicode_safe\n"
        "make_stdio_unicode_safe()\n"
        "print('\\u2713', sys.stdout.errors)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env, capture_output=True, timeout=120
    )
    assert result.returncode == 0
    assert result.stdout.decode("utf-8").split() == ["✓", "strict"]
