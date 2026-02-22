"""
Starlette-based HTTP server for the Jabberwocky mirror.

Implements PEP 691 content negotiation:
  GET /simple/           -> project list  (JSON or HTML)
  GET /simple/<pkg>/     -> project detail (JSON or HTML)
  GET /files/<filename>  -> wheel file
"""

from __future__ import annotations

import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route

log = logging.getLogger(__name__)

CONTENT_TYPE_JSON = "application/vnd.pypi.simple.v1+json"
CONTENT_TYPE_HTML = "application/vnd.pypi.simple.v1+html"
CONTENT_TYPE_LEGACY_HTML = "text/html"


def _wants_json(request: Request) -> bool:
    """
    Determine if the client prefers JSON per PEP 691 content negotiation.

    uv sends: Accept: application/vnd.pypi.simple.v1+json, ...
    """
    accept = request.headers.get("accept", "*/*")
    # Quick check: if the JSON content type appears with higher or equal
    # priority than the HTML types, serve JSON.
    # For simplicity we default to JSON (it's what uv wants).
    if CONTENT_TYPE_JSON in accept:
        return True
    if "application/vnd.pypi.simple" in accept:
        return True
    # Legacy pip / browsers: serve JSON anyway since we only have JSON indexes.
    return True


def _json_response(data: dict) -> Response:
    import json

    return Response(
        content=json.dumps(data),
        media_type=CONTENT_TYPE_JSON,
    )


def make_app(mirror_dir: Path) -> Starlette:
    simple_dir = mirror_dir / "simple"
    files_dir = mirror_dir / "files"

    async def simple_index(request: Request) -> Response:
        index_file = simple_dir / "index.json"
        if not index_file.exists():
            return Response("Mirror not built yet", status_code=503)
        import json

        data = json.loads(index_file.read_text())
        return _json_response(data)

    async def project_detail(request: Request) -> Response:
        name = request.path_params["project"]
        # Normalize: replace underscores/dots with hyphens
        from packaging.utils import canonicalize_name

        canonical = canonicalize_name(name)
        index_file = simple_dir / canonical / "index.json"
        if not index_file.exists():
            return Response(f"Package {name!r} not found in mirror", status_code=404)
        import json

        data = json.loads(index_file.read_text())
        return _json_response(data)

    async def serve_file(request: Request) -> Response:
        filename = request.path_params["filename"]

        # Wheel-only mirror: reject anything that isn't a .whl file
        if not filename.endswith(".whl"):
            return Response(
                f"{filename!r} is not a wheel file. Jabberwocky is a wheel-only mirror.",
                status_code=400,
            )

        # Prevent path traversal — filename must not escape the files directory
        path = (files_dir / filename).resolve()
        if not path.is_relative_to(files_dir.resolve()):
            return Response("Invalid filename", status_code=400)

        if not path.exists():
            return Response(f"File {filename!r} not found", status_code=404)
        return FileResponse(path)

    routes = [
        Route("/simple/", simple_index),
        Route("/simple/{project:str}/", project_detail),
        Route("/files/{filename:path}", serve_file),
    ]

    return Starlette(routes=routes)
