import pathlib
import tempfile

import pytest

from app.core import config, db


@pytest.fixture(autouse=True, scope="session")
def _temp_db():
    tmp = pathlib.Path(tempfile.mkdtemp()) / "test_meet2mom.db"
    original_config_path = config.DB_PATH
    original_db_path = db.DB_PATH
    config.DB_PATH = tmp
    db.DB_PATH = tmp
    db.init_db()
    yield
    config.DB_PATH = original_config_path
    db.DB_PATH = original_db_path
