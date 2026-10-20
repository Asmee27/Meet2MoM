from __future__ import annotations

import sys


def make_stdio_unicode_safe() -> None:
    """
    Stop console output from crashing the pipeline on non-UTF-8 streams.

    The pipeline prints status symbols such as "✓" and "⚠". When stdout or
    stderr is redirected on Windows the stream encoding is usually cp1252, and
    printing those symbols raises UnicodeEncodeError, which failed whole jobs.
    Keep the stream's encoding but escape characters it cannot represent.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if stream is None or encoding == "utf8":
            continue
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            # Not a TextIOWrapper (e.g. a test capture object); leave it alone.
            pass
