from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import BoundedSemaphore

from fastapi import FastAPI

from atlas_api.api.v1 import router
from atlas_api.config import (
    RUNTIME_DATABASE_ENV,
    RUNTIME_ROLE,
    DeliverySettings,
    read_database_url,
)
from atlas_api.db import create_runtime_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = DeliverySettings.from_environment()
    app.state.delivery_settings = settings
    app.state.delivery_slots = BoundedSemaphore(settings.file_slots)
    url = read_database_url(RUNTIME_DATABASE_ENV, RUNTIME_ROLE)
    engine = create_runtime_engine(url) if url is not None else None
    app.state.database_engine = engine
    try:
        yield
    finally:
        if engine is not None:
            engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Open 3D Atlas API", version="1.0.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
