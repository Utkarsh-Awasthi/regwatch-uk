import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from regwatch import db
    return db.connect(tmp_path / "t.db")
