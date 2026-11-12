"""SQLAlchemy 引擎与会话。SQLite 下开 WAL，worker 线程和 API 线程可以同时读写。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

if config.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=8000")
        cur.close()


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(engine)
    # 轻量迁移：老库没有 user_id，create_all 不会补列
    if config.DATABASE_URL.startswith("sqlite"):
        from sqlalchemy import inspect, text
        cols = {c["name"] for c in inspect(engine).get_columns("projects")}
        if "user_id" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE projects ADD COLUMN user_id VARCHAR(32) DEFAULT ''"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_projects_user_id ON projects (user_id)"))
        if "visitor_id" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE projects ADD COLUMN visitor_id VARCHAR(64) DEFAULT ''"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_projects_visitor_id ON projects (visitor_id)"))
