# tests/conftest.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.base import Base
from models import member, scheduling  # noqa: F401 (register models)


@pytest.fixture()
def session():
    # SQLite in-memory DB just for tests
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
