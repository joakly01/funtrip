import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from audit_agent_runtime.config.settings import Settings
from audit_agent_runtime.utils.json_utils import safe_json_dumps


def build_engine(settings: Settings):
    cached = getattr(settings, "_engine", None)
    if cached is not None:
        return cached

    if settings.database_url.startswith("sqlite+pysqlite:///:memory:"):
        engine = create_engine(
            settings.database_url,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        object.__setattr__(settings, "_engine", engine)
        return engine

    engine = create_engine(
        settings.database_url,
        future=True,
        # 内网 PostgreSQL 或宿主重启后，连接池中可能仍保留已失效的 TCP 连接。
        # checkout 前探活并回收断连，避免第一次本体查询无意义地返回 500。
        pool_pre_ping=True,
        json_serializer=lambda payload: safe_json_dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    object.__setattr__(settings, "_engine", engine)
    return engine


def build_session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = build_engine(settings)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)
