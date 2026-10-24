import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import os

# Unit tests must not load real embedding models. The production default
# fallback extractor is "hybrid" (learned model); tests that exercise it set
# the variable explicitly (tests/test_dialogue_model.py).
os.environ.setdefault("FALLBACK_EXTRACTOR", "rules")

from app.core import config

# Redirect storage and the database to a throwaway directory BEFORE any other
# app module is imported. Modules such as app.core.storage, claim_writer,
# transcript_index and the pipeline adapter bind STORAGE_ROOT at import time,
# so patching later (inside a fixture) would let tests write into the real
# storage/ directory.
# resolve() expands Windows 8.3 short names (e.g. ASMEEJ~1) so the root is
# canonical, like the real STORAGE_ROOT derived from Path(__file__).resolve().
_TEST_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="meet2mom_tests_")).resolve()
_ORIGINAL_STORAGE_ROOT = config.STORAGE_ROOT
_ORIGINAL_DB_PATH = config.DB_PATH
config.STORAGE_ROOT = _TEST_ROOT / "storage"
config.DB_PATH = _TEST_ROOT / "test_meet2mom.db"

from app.core import db  # noqa: E402  (must follow the config redirect)

# Tests never use the developer's real LLM configuration from .env: start every
# session with the stub provider and no keys. Tests that need a provider patch
# config themselves and fake the HTTP layer.
# The legacy suites predate accounts; Phase 7 auth tests switch this on.
config.AUTH_REQUIRED = False
config.LLM_PROVIDER = "stub"
config.LLM_MODEL = "mock-v1"
for _key in ("LLM_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
    setattr(config, _key, None)

# Tests never send real email: development (console) provider, no Brevo key
# or webhook token from .env, and no background dispatcher. Email tests call
# outbox.dispatch_pending() themselves with a fake sender.
config.EMAIL_PROVIDER = "console"
config.EMAIL_DISPATCH = "off"
for _key in ("BREVO_API_KEY", "EMAIL_SENDER_ADDRESS", "BREVO_WEBHOOK_TOKEN"):
    setattr(config, _key, None)
config.PUBLIC_APP_URL = "http://app.test"


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    """Safety net: an unpatched urllib request to a non-local host fails the test."""
    import urllib.request
    from urllib.parse import urlparse

    real_urlopen = urllib.request.urlopen

    def guarded(url, *args, **kwargs):
        target = getattr(url, "full_url", url)
        host = urlparse(str(target)).hostname or ""
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise RuntimeError(f"Test tried to make a real HTTP request to {host}")
        return real_urlopen(url, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", guarded)


@pytest.fixture(autouse=True, scope="session")
def _temp_db():
    original_db_path = db.DB_PATH
    db.DB_PATH = config.DB_PATH
    db.init_db()
    yield
    db.DB_PATH = original_db_path
    config.DB_PATH = _ORIGINAL_DB_PATH
    config.STORAGE_ROOT = _ORIGINAL_STORAGE_ROOT
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)
