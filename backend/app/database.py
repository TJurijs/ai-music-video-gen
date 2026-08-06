from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import event, inspect, text
from sqlalchemy.pool import NullPool
from app.config import settings
from datetime import datetime
import os
import sqlite3

os.makedirs(settings.storage_dir, exist_ok=True)

# DB lives next to the backend dir regardless of where uvicorn is launched from
_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "musicvideo.db")
DATABASE_URL = f"sqlite:///{_DB_PATH}"
_STARTUP_BACKUP: str | None = None
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    # Scene workers can remain alive while remote video jobs render. A bounded
    # QueuePool made the 16th concurrent scene time out before provider
    # submission. SQLite connections are cheap; give each short-lived request
    # or worker session its own connection and close it with the session.
    poolclass=NullPool,
)


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    _auto_migrate_columns()


def backup_database(reason: str = "schema migration") -> str | None:
    """Create one consistent SQLite backup before the first startup mutation."""
    global _STARTUP_BACKUP
    if _STARTUP_BACKUP or not os.path.isfile(_DB_PATH):
        return _STARTUP_BACKUP
    backup_dir = os.path.join(os.path.dirname(_DB_PATH), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    destination = os.path.join(backup_dir, f"musicvideo-{stamp}.db")
    source_connection = sqlite3.connect(_DB_PATH, timeout=30)
    destination_connection = sqlite3.connect(destination, timeout=30)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    _STARTUP_BACKUP = destination
    print(f"[startup] database backup created before {reason}: {destination}")
    return destination


def ensure_integrity_indexes() -> None:
    """Add race-proof invariants that SQLite can enforce on existing DBs."""
    statements = [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_scene_project_order "
        "ON scene(project_id, \"order\")",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_song_project "
        "ON song(project_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sceneasset_active "
        "ON sceneasset(scene_id, asset_type) WHERE is_active = 1",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_promptversion_active "
        "ON scenepromptversion(scene_id, prompt_type) WHERE is_active = 1",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_characterasset_active "
        "ON characterasset(character_id) WHERE is_active = 1",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_project_active_assembly "
        "ON generationjob(project_id) "
        "WHERE job_type = 'assembly' AND status = 'running'",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


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
    backup_created = False
    for table in SQLModel.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {col["name"] for col in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            type_str = str(col.type).upper().split("(")[0].strip()
            sql_type = sqlite_types.get(type_str, "TEXT")
            if not backup_created:
                backup_database("automatic column migration")
                backup_created = True
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
