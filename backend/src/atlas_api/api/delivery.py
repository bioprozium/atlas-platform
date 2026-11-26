"""Public endpoint contracts for manifests and verified GLB downloads."""

from collections.abc import Awaitable, Callable
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from atlas_api.anatomy.router import PublicCode
from atlas_api.anatomy.schemas import ErrorResponse
from atlas_api.api.dependencies import read_connection
from atlas_api.assets import queries as registry
from atlas_api.assets import router as assets
from atlas_api.assets.delivery import (
    DELIVERY_BUSY,
    GLB_MIME,
    STORAGE_UNAVAILABLE,
    AssetFileResponse,
    StoredObject,
    cache_headers,
    matches_etag,
    open_object,
)
from atlas_api.assets.validation import GLBValidationError
from atlas_api.scenes import queries as scenes
from atlas_api.scenes import router as scene_routes
from atlas_api.scenes.manifest import SceneManifest, build_manifest


class NoStoreRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def no_store_errors(request: Request) -> Response:
            try:
                return await handler(request)
            except HTTPException as error:
                response = await http_exception_handler(request, error)
            except RequestValidationError as error:
                response = await request_validation_exception_handler(request, error)
            response.headers["Cache-Control"] = "no-store"
            return response

        return no_store_errors


router = APIRouter(route_class=NoStoreRoute)
ERROR_HEADERS = {
    "Cache-Control": {"description": "Do not store errors.", "schema": {"type": "string"}}
}
CONDITIONAL_REQUEST = {
    "parameters": [
        {
            "in": "header",
            "name": "If-None-Match",
            "required": False,
            "description": "Return 304 for an available matching representation.",
            "schema": {"type": "string"},
        }
    ],
    "responses": {"422": {"headers": ERROR_HEADERS}},
}


def conditional_value(request: Request) -> str | None:
    values = request.headers.getlist("if-none-match")
    return ",".join(values) if values else None


SUCCESS_HEADERS = {
    "ETag": {"description": "Strong SHA-256 validator.", "schema": {"type": "string"}},
    "Cache-Control": {
        "description": "Revalidate before cache reuse and preserve the representation bytes.",
        "schema": {"type": "string"},
    },
    "X-Content-Type-Options": {
        "description": "Prevents content type sniffing.",
        "schema": {"type": "string", "const": "nosniff"},
    },
}
NOT_MODIFIED = {
    304: {"description": "The available representation matches.", "headers": SUCCESS_HEADERS}
}


@router.get(
    "/scenes/{code}/versions/{version}/manifest",
    tags=["Scenes"],
    operation_id="getSceneManifest",
    response_model=SceneManifest,
    openapi_extra=CONDITIONAL_REQUEST,
    responses={
        404: {**scene_routes.VERSION_NOT_FOUND[404], "headers": ERROR_HEADERS},
        **NOT_MODIFIED,
        200: {"headers": SUCCESS_HEADERS},
        503: {
            "model": ErrorResponse,
            "description": (
                "Database, dependencies, storage or capacity unavailable, or manifest limit exceeded."
            ),
            "headers": ERROR_HEADERS,
        },
    },
)
def get_scene_manifest(
    request: Request,
    code: PublicCode,
    version: assets.VersionIdentifier,
) -> Response:
    """Retrieve the complete offline manifest for an exact published scene version."""
    settings = request.app.state.delivery_settings
    with read_connection(request, scene_routes.DATABASE_UNAVAILABLE) as connection:
        scene = scene_routes.require_available(
            scenes.get_version(connection, code, version), "Scene version not found."
        )
        body, objects = build_manifest(connection, scene, settings)

    if objects:
        slots = request.app.state.delivery_slots
        if not slots.acquire(blocking=False):
            raise HTTPException(503, DELIVERY_BUSY)
        try:
            for obj in dict.fromkeys(objects):
                with open_object(settings.storage_root, obj):
                    pass
        except (OSError, GLBValidationError):
            raise HTTPException(503, STORAGE_UNAVAILABLE) from None
        finally:
            slots.release()
    headers = cache_headers(sha256(body).hexdigest())
    if matches_etag(conditional_value(request), headers["ETag"]):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@router.get(
    "/assets/{code}/versions/{version}/file",
    tags=["Assets"],
    operation_id="getAssetFile",
    response_class=Response,
    openapi_extra=CONDITIONAL_REQUEST,
    responses={
        404: {**assets.VERSION_NOT_FOUND[404], "headers": ERROR_HEADERS},
        **NOT_MODIFIED,
        200: {
            "headers": {
                **SUCCESS_HEADERS,
                "Content-Length": {
                    "description": "Verified byte length of the GLB.",
                    "schema": {"type": "integer"},
                },
            },
            "content": {GLB_MIME: {"schema": {"type": "string", "format": "binary"}}},
        },
        503: {
            "model": ErrorResponse,
            "description": "Asset registry, published asset storage, or delivery capacity unavailable.",
            "headers": ERROR_HEADERS,
        },
    },
)
def get_asset_file(
    request: Request,
    code: PublicCode,
    version: assets.VersionIdentifier,
) -> AssetFileResponse:
    """Download the complete verified GLB for an exact published asset version."""
    v = registry.version
    with read_connection(request, assets.DATABASE_UNAVAILABLE) as connection:
        row = registry.exact_version(
            connection, code, version, v.sha256, v.file_size, v.storage_reference
        )
        if row is None:
            raise HTTPException(404, "Asset version not found.")
        obj = StoredObject(row.sha256, row.file_size, row.storage_reference)

    return AssetFileResponse(
        request.app.state.delivery_settings.storage_root,
        obj,
        request.app.state.delivery_slots,
        conditional_value(request),
    )
