"""The production web server (deploy/aws/nginx.conf) on slow connections (CMPS271-137).

CloudFront's CachingDisabled policy does not compress, so nginx compresses the pages and built
files itself. API answers stay uncompressed. Built files keep for a year because their names
change with their content.
"""

import http.server
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = (ROOT / "deploy/aws/nginx.conf").read_text(encoding="utf-8")
YEAR = "public, max-age=31536000, immutable"


def block(start: str) -> str:
    """The text of the location block that begins with `start`, nested blocks included."""
    at = CONFIG.index(start)
    depth, i = 0, CONFIG.index("{", at)
    while True:
        depth += {"{": 1, "}": -1}.get(CONFIG[i], 0)
        if depth == 0:
            return CONFIG[at:i + 1]
        i += 1


def test_pages_and_built_files_are_compressed_and_api_answers_are_not():
    pages = block("location / {")
    assert "gzip on;" in pages
    # CloudFront sends a Via header; without this nginx treats every request as proxied and skips it.
    assert "gzip_proxied any;" in pages
    assert "application/javascript" in pages and "text/css" in pages
    assert "gzip" not in block("location ~ ^/(auth|")
    assert "gzip" not in block("location /admin-api/ {")
    assert not re.search(r"^\s*gzip", CONFIG.split("location", 1)[0], re.M), "server-wide gzip would reach the API"


def test_built_files_keep_for_a_year_and_a_missing_one_is_not_the_page():
    assets = block("location /assets/ {")
    assert YEAR in assets
    assert "try_files $uri =404;" in assets


# The real server, when nginx is installed (GitHub's Ubuntu runners have it).
NGINX = shutil.which("nginx")
MIME = Path("/etc/nginx/mime.types")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"guides": "' + b"x" * 4000 + b'"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def web(tmp_path):
    site = tmp_path / "html"
    (site / "assets").mkdir(parents=True)
    (site / "index.html").write_text("<!doctype html><title>Taasheera</title>" + "<p>page</p>" * 200)
    (site / "assets/index-abc123.js").write_text("export const words = 'visa';\n" * 2000)
    api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    port = free_port()
    (tmp_path / "site.conf").write_text(
        CONFIG.replace("${ORIGIN_TOKEN}", "token").replace("listen 80;", f"listen 127.0.0.1:{port};")
        .replace("http://api:8000", f"http://127.0.0.1:{api.server_port}").replace("/usr/share/nginx/html", str(site)))
    # Like the nginx:stable-alpine image: gzip is off unless the site turns it on.
    (tmp_path / "nginx.conf").write_text(
        # As root, nginx's workers would switch to "nobody", who cannot read pytest's private folder.
        ("user root;\n" if os.geteuid() == 0 else "") + f"pid {tmp_path}/nginx.pid;\nevents {{}}\nhttp {{\n  include {MIME};\n  default_type application/octet-stream;\n"
        f"  access_log off;\n  include {tmp_path}/site.conf;\n}}\n")
    server = subprocess.Popen([NGINX, "-p", str(tmp_path), "-c", str(tmp_path / "nginx.conf"), "-e", str(tmp_path / "error.log"),
                               "-g", "daemon off;"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            if server.poll() is not None:
                pytest.fail(server.stderr.read().decode())
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.terminate()
    server.wait(timeout=5)
    api.shutdown()


def get(url: str):
    """Fetch like CloudFront: origin token, Via header, and a viewer that accepts gzip."""
    request = urllib.request.Request(url, headers={
        "X-Taasheera-Origin": "token", "Via": "1.1 abc.cloudfront.net (CloudFront)", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


@pytest.mark.skipif(not NGINX or not MIME.exists(), reason="needs nginx")
def test_real_nginx_compresses_pages_and_built_files_through_cloudfront(web):
    status, headers, body = get(web + "/assets/index-abc123.js")
    assert status == 200 and headers["Content-Encoding"] == "gzip"
    assert len(body) < 2000 * 29 / 10
    assert headers["Cache-Control"] == YEAR

    status, headers, _ = get(web + "/visas/schengen-short-stay")
    assert status == 200 and headers["Content-Encoding"] == "gzip"
    assert headers["Cache-Control"] == "no-cache"


@pytest.mark.skipif(not NGINX or not MIME.exists(), reason="needs nginx")
def test_real_nginx_leaves_api_answers_uncompressed_and_missing_files_missing(web):
    for path in ("/visa-guides", "/auth/me", "/admin-api/users"):
        status, headers, _ = get(web + path)
        assert status == 200 and headers["Content-Encoding"] is None, path

    status, headers, body = get(web + "/assets/index-old.js")
    assert status == 404 and b"Taasheera" not in body
    assert headers["Cache-Control"] != YEAR
