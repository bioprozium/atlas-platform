from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from atlas_api.anatomy.router import router as anatomy_router
from atlas_api.assets.router import router as asset_router
from atlas_api.db import database_is_ready

router = APIRouter(prefix="/api/v1")
router.include_router(anatomy_router)
router.include_router(asset_router)


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready"]


class NotReadyResponse(BaseModel):
    status: Literal["unavailable"]


@router.get("/health", operation_id="getHealth")
def health() -> HealthResponse:
    """Check whether the API process is responding."""
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    operation_id="getReadiness",
    response_model=ReadinessResponse,
    responses={
        503: {
            "model": NotReadyResponse,
            "description": "The database connection is unavailable.",
        }
    },
)
def readiness(request: Request) -> ReadinessResponse | JSONResponse:
    """Check whether the API can query PostgreSQL."""
    if database_is_ready(request.app.state.database_engine):
        return ReadinessResponse(status="ready")
    return JSONResponse(
        status_code=503,
        content=NotReadyResponse(status="unavailable").model_dump(),
    )
