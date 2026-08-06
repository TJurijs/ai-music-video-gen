from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine


@pytest.fixture
def test_engine(tmp_path: Path):
    database_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
