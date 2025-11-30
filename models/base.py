import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()  # loads DATABASE_URL from .env if present

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. "
        "Set it in your environment or .env file (see README)."
    )

engine = create_engine(DATABASE_URL, echo=False, future=True)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
    expire_on_commit=False,
)

Base = declarative_base()


def get_session():
    """Helper to get a new DB session."""
    return SessionLocal()
