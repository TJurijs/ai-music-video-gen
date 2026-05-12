from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import inspect, text
from app.config import settings
import os

os.makedirs(settings.storage_dir, exist_ok=True)

# DB lives next to the backend dir regardless of where uvicorn is launched from
_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "musicvideo.db")
DATABASE_URL = f"sqlite:///{_DB_PATH}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    _auto_migrate_columns()


def _auto_migrate_columns():
    """Add any columns defined in models but missing from the existing SQLite DB.

    Lightweight dev-mode migration — no DROP/RENAME, just ADD COLUMN for new
    optional fields. Production would use Alembic.
    """
    insp = inspect(engine)
    sqlite_types = {
        "INTEGER": "INTEGER", "TEXT": "TEXT", "REAL": "REAL",
        "BOOLEAN": "INTEGER", "VARCHAR": "TEXT", "FLOAT": "REAL",
        "JSON": "TEXT", "DATETIME": "TIMESTAMP",
    }
    for table in SQLModel.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {col["name"] for col in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            type_str = str(col.type).upper().split("(")[0].strip()
            sql_type = sqlite_types.get(type_str, "TEXT")
            with engine.connect() as conn:
                try:
                    conn.execute(text(
                        f'ALTER TABLE {table.name} ADD COLUMN {col.name} {sql_type}'
                    ))
                    conn.commit()
                    print(f"[migration] Added {table.name}.{col.name} ({sql_type})")
                except Exception as e:
                    print(f"[migration] Skipped {table.name}.{col.name}: {e}")


def get_session():
    with Session(engine) as session:
        yield session
