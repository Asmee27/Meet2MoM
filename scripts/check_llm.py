"""
Check the LLM configuration without printing secrets (Phase 5).

    python scripts/check_llm.py            # configuration only, no network call
    python scripts/check_llm.py --live     # plus ONE small synthetic request

The API key is never printed: only whether one is configured. The model name
is printed only when it is a valid model id (a malformed .env line could
otherwise put another setting's secret into it). --live sends a synthetic
two-line transcript, never meeting data, through the same LLMClient and
extraction prompt the pipeline uses, with a strict timeout and no retries.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SYNTHETIC_WINDOW = (
    "Transcript Window [w0000]:\n"
    "[u00000] SPEAKER_00 (00:00) Alice: We decided to ship the demo build on Thursday.\n"
    "[u00001] SPEAKER_01 (00:05) Bob: Agreed. I will write the release notes by Wednesday."
)


def configuration() -> dict:
    from app.core import config
    from app.core.llm_client import model_name_is_valid, resolve_api_key, resolve_model
    from app.services.intelligence.extraction_pipeline import llm_unavailable_reason

    provider = (config.LLM_PROVIDER or "stub").strip().lower()
    model = resolve_model(provider) or ""
    valid = bool(model) and model_name_is_valid(model)
    return {
        "provider": provider,
        "model": model if valid else None,
        "model_valid": valid,
        "api_key_configured": bool(resolve_api_key(provider)),
        "extractor": config.EXTRACTOR,
        "timeout_seconds": config.LLM_TIMEOUT_SECONDS,
        "timeout_retries": config.LLM_TIMEOUT_RETRIES,
        "extraction_budget_seconds": config.LLM_EXTRACTION_BUDGET_SECONDS,
        "ready": llm_unavailable_reason() is None,
        "problem": llm_unavailable_reason(),
    }


def live_request(timeout: float) -> dict:
    from app.core.llm_client import LLMClient, LLMClientError
    from app.services.intelligence.llm_extractor import SYSTEM_PROMPT, validate_response

    client = LLMClient(timeout=timeout, backoff_delays=[])  # one attempt only
    started = time.monotonic()
    try:
        data = validate_response(client.complete_json(system=SYSTEM_PROMPT, user=SYNTHETIC_WINDOW))
    except LLMClientError as exc:  # messages are built by the client and key-redacted
        return {"success": False, "error": str(exc)[:300],
                "elapsed_seconds": round(time.monotonic() - started, 2)}
    return {
        "success": True,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "provider_seconds": round(client.provider_seconds, 2),
        "decisions": len(data.get("decisions") or []),
        "actions": len(data.get("actions") or []),
        "cited_ids": sorted({u for item in (data.get("decisions") or []) + (data.get("actions") or [])
                             if isinstance(item, dict) for u in (item.get("evidence") or [])}),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--live", action="store_true", help="make one small synthetic request")
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds for the live request")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)  # keep library logs out of the output

    info = configuration()
    print(json.dumps(info, indent=2))
    if not args.live:
        print("LLM ready" if info["ready"] else f"LLM NOT ready: {info['problem']}")
        return 0 if info["ready"] else 1
    if not info["ready"]:
        print(f"Skipping the live request: {info['problem']}")
        return 1
    result = live_request(args.timeout)
    print(json.dumps(result, indent=2))
    return 0 if result["success"] else 2


if __name__ == "__main__":
    sys.exit(main())
