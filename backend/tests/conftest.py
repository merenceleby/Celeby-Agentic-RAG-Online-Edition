import pytest

from config import settings
from services.db import init_db

_KEYS = ("state_dir", "usage_db", "app_db", "eval_dir", "docs_dir")


@pytest.fixture(autouse=True)
def isolated_state(tmp_path):
    """Her test kendi geçici veritabanı ve klasörleriyle çalışır."""
    old = {k: getattr(settings, k) for k in _KEYS}
    (tmp_path / "docs").mkdir()
    new = {"state_dir": tmp_path, "usage_db": tmp_path / "usage.db", "app_db": tmp_path / "app.db",
           "eval_dir": tmp_path / "eval", "docs_dir": tmp_path / "docs"}
    for k, v in new.items():
        object.__setattr__(settings, k, v)
    init_db()
    yield tmp_path
    for k, v in old.items():
        object.__setattr__(settings, k, v)
