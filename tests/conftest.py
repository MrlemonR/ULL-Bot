"""Testler gerçek ev dizinine, gerçek DB'ye ve gerçek audit log'a dokunmaz.

`settings` modül seviyesinde bir singleton olduğu için ayarlar monkeypatch ile
geçici olarak değiştirilir; sandbox config önbelleği her testte tazelenir.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.connection import init_db
from app.safety import sandbox
from app.settings import Settings, settings


@pytest.fixture
def workspace(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """İzinli bir çalışma alanı + izole DB/audit dizini kur."""
    root = Path(tmp_path) / "workspace"
    root.mkdir()
    data = Path(tmp_path) / "data"  # çalışma alanının DIŞINDA (denied_paths'e giriyor)
    data.mkdir()

    monkeypatch.setattr(settings, "workspace_root", str(root))
    monkeypatch.setattr(settings, "db_path", str(data / "test.db"))
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(Settings, "data_dir", property(lambda self: data))

    sandbox.get_workspace_config(refresh=True)
    init_db()
    yield root
    sandbox._cached_config = None


@pytest.fixture
def home(tmp_path: pytest.TempPathFactory) -> Path:
    """Testlerde `rm -rf` kurallarının ölçüldüğü sahte ev dizini."""
    path = Path(tmp_path) / "home"
    path.mkdir(exist_ok=True)
    return path
