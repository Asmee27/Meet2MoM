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
