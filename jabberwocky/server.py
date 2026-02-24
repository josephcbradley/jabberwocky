"""
Standard library HTTP server for the Jabberwocky mirror.

Implements PEP 691 content negotiation:
  GET /simple/           -> project list  (JSON)
  GET /simple/<pkg>/     -> project detail (JSON)
  GET /files/<filename>  -> wheel file
"""

from __future__ import annotations

import argparse
import http.server
import logging
import re
import socketserver
from pathlib import Path
from urllib.parse import unquote

log = logging.getLogger(__name__)

CONTENT_TYPE_JSON = "application/vnd.pypi.simple.v1+json"
CONTENT_TYPE_HTML = "text/html"


def canonicalize_name(name: str) -> str:
    # PEP 503 normalization
    return re.sub(r"[-_.]+", "-", name).lower()


class MirrorHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, mirror_dir: Path, *args, **kwargs):
        self.mirror_dir = mirror_dir.resolve()
        self.simple_dir = self.mirror_dir / "simple"
        self.files_dir = self.mirror_dir / "files"
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        # Use standard logging
        log.info(
            "%s - - [%s] %s\n"
            % (self.client_address[0], self.log_date_time_string(), format % args)
        )

    def do_GET(self):
        path = unquote(self.path).rstrip("/")

        # Determine content type preference (for logging mainly, we default to JSON)
        # accept = self.headers.get("Accept", "*/*")

        if path == "/simple":
            self.serve_simple_index()
        elif path.startswith("/simple/"):
            # Project detail
            project = path[len("/simple/") :]
            if "/" in project:
                self.send_error(404, "Not Found")
                return
            self.serve_project_detail(project)
        elif path.startswith("/files/"):
            filename = path[len("/files/") :]
            self.serve_file(filename)
        else:
            self.send_error(404, "Not Found")

    def serve_simple_index(self):
        index_file = self.simple_dir / "index.json"
        if not index_file.exists():
            self.send_error(503, "Mirror not built yet")
            return

        try:
            data = index_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_JSON)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            log.error(f"Error serving index: {e}")
            self.send_error(500)

    def serve_project_detail(self, project: str):
        canonical = canonicalize_name(project)
        index_file = self.simple_dir / canonical / "index.json"

        if not index_file.exists():
            self.send_error(404, f"Package {project!r} not found in mirror")
            return

        try:
            data = index_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_JSON)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            log.error(f"Error serving project detail: {e}")
            self.send_error(500)

    def serve_file(self, filename: str):
        if not filename.endswith(".whl"):
            self.send_error(400, "Only wheels are served")
            return

        # Security check: path traversal
        try:
            path = (self.files_dir / filename).resolve()
        except Exception:
            self.send_error(400, "Invalid path")
            return

        # Ensure path is within files_dir
        # resolve() handles symlinks, but we should verify base.
        # Note: on some systems resolve() might need file existence.
        # But we check strict prefix.
        if not str(path).startswith(str(self.files_dir.resolve())):
            self.send_error(400, "Invalid filename")
            return

        if not path.exists():
            self.send_error(404, "File not found")
            return

        try:
            with open(path, "rb") as f:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                fs = path.stat()
                self.send_header("Content-Length", str(fs.st_size))
                self.end_headers()
                import shutil

                shutil.copyfileobj(f, self.wfile)
        except Exception as e:
            log.error(f"Error serving file: {e}")


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Handle requests in a separate thread."""

    daemon_threads = True


def run(mirror_dir: Path, host: str, port: int):
    # Partial function to pass mirror_dir to handler
    def handler(*args, **kwargs):
        return MirrorHandler(mirror_dir, *args, **kwargs)

    server = ThreadedHTTPServer((host, port), handler)
    print(f"Serving mirror at http://{host}:{port}/simple/")
    print(f"Configure uv: uv add --index http://{host}:{port}/simple/ <package>")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve Jabberwocky mirror")
    parser.add_argument(
        "--mirror", type=Path, default=Path("mirror"), help="Mirror directory"
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host interface")
    parser.add_argument("--port", type=int, default=8080, help="Port")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    run(args.mirror, args.host, args.port)
