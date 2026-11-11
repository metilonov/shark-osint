from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import pytest_asyncio

from config import Settings
from database.models import Observation
from database.storage import Storage


@pytest.fixture
def settings(tmp_path):
    return Settings(admin_id=1001, data_dir=tmp_path, cooldown_seconds=10)


@pytest_asyncio.fixture
async def storage(tmp_path):
    db = Storage(tmp_path / "test.sqlite3")
    await db.open()
    yield db
    await db.close()


@pytest.fixture
def observations():
    return [Observation("Sherlock", "Example", "https://example.com/u/Owner", "claimed", 200),
            Observation("Maigret", "Example", "https://www.example.com/u/Owner/?utm_source=test", "claimed", 200,
                        metadata={"display_name": "Public display name"}),
            Observation("Sherlock", "Uncertain", "https://other.example/u/Owner", "claimed"),
            Observation("Maigret", "Denied", "https://denied.example/u/Owner", "unknown", 429, error="Rate limited")]
