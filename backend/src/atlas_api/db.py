from sqlalchemy import Engine, MetaData, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

SCHEMA = "atlas"
metadata = MetaData(schema=SCHEMA)


def create_runtime_engine(url: URL) -> Engine:
    return create_engine(
        url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=2,
        connect_args={"connect_timeout": 3},
    )


def database_is_ready(engine: Engine | None) -> bool:
    if engine is None:
        return False

    try:
        with engine.connect() as connection:
            return connection.scalar(text("SELECT 1")) == 1
    except SQLAlchemyError:
        return False
