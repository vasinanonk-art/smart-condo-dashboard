#!/usr/bin/env python3
"""Authenticated, read-only Xiaomi camera preview gateway."""
from __future__ import annotations

import argparse
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CAMERA_ID = "xiaomi-camera-1"
SNAPSHOT_STREAM_NAME = "living_room_xiaomi"
LIVE_STREAM_NAME = "living_room_xiaomi_h264"
PREVIEW_ASSET_VERSION = "v1022-xiaomi-livefix1"
MAX_JSON_BYTES = 2_000_000
MAX_SNAPSHOT_BYTES = 5_000_000
MAX_PLAYLIST_BYTES = 64_000
MAX_SEGMENT_BYTES = 4_000_000
HLS_CHILD = re.compile(r"^hls/playlist\.m3u8\?id=([A-Za-z0-9_-]{1,64})$", re.MULTILINE)
HLS_SEGMENT = re.compile(r"^segment\.ts\?id=([A-Za-z0-9_-]{1,64})&n=([0-9]{1,7})$", re.MULTILINE)


def loopback_base(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip().rstrip("/"))
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("preview_upstream_must_be_loopback")
    return value.strip().rstrip("/")


class PreviewHandler(SimpleHTTPRequestHandler):
    production_url = "http://127.0.0.1:8090"
    go2rtc_url = "http://127.0.0.1:2984"

    def _static_template(self, filename: str, content_type: str) -> None:
        try:
            content = (Path(self.directory) / filename).read_text(encoding="utf-8")
        except OSError:
            self._json_error(404, "not_found")
            return
        body = (
            content.replace("__ASSET_VERSION__", PREVIEW_ASSET_VERSION)
            .replace("__CHART_DEBUG__", "false")
            .encode()
        )
        self._send(200, content_type, body)

    def _cookie(self) -> str:
        return self.headers.get("Cookie", "")[:8192]

    def _request(self, base: str, path: str, *, method: str = "GET", body: bytes | None = None):
        headers = {"User-Agent": "smart-condo-preview/1.0"}
        if cookie := self._cookie():
            headers["Cookie"] = cookie
        content_type = self.headers.get("Content-Type")
        if content_type and method == "POST":
            headers["Content-Type"] = content_type[:128]
        request = urllib.request.Request(base + path, data=body, headers=headers, method=method)
        return urllib.request.urlopen(request, timeout=20)

    def _send(self, status: int, content_type: str, body: bytes, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, status: int, detail: str) -> None:
        self._send(status, "application/json", json.dumps({"detail": detail}).encode())

    def _authenticated(self) -> bool:
        try:
            with self._request(self.production_url, "/api/auth/status") as response:
                payload = json.loads(response.read(64_001))
        except Exception:
            return False
        return response.status == 200 and isinstance(payload, dict) and payload.get("authenticated") is True

    def _bridge_available(self) -> bool:
        query = urllib.parse.urlencode({"src": SNAPSHOT_STREAM_NAME})
        try:
            with self._request(self.go2rtc_url, f"/api/streams?{query}") as response:
                payload = json.loads(response.read(256_001))
        except Exception:
            return False
        return response.status == 200 and isinstance(payload, dict) and bool(
            payload.get("producers") or SNAPSHOT_STREAM_NAME in payload
        )

    def _auth_proxy(self, *, method: str = "GET") -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length < 0 or length > 64_000:
            self._json_error(413, "request_too_large")
            return
        body = self.rfile.read(length) if length else None
        try:
            with self._request(self.production_url, self.path, method=method, body=body) as response:
                content = response.read(128_001)
                self._send(
                    response.status,
                    response.headers.get("Content-Type", "application/json"),
                    content,
                    Set_Cookie=response.headers.get("Set-Cookie", ""),
                )
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.headers.get("Content-Type", "application/json"), exc.read(128_001))
        except Exception:
            self._json_error(502, "authentication_unavailable")

    def _devices(self) -> None:
        try:
            with self._request(self.production_url, "/api/camera-control/devices") as response:
                payload = json.loads(response.read(MAX_JSON_BYTES + 1))
        except Exception:
            self._json_error(502, "camera_inventory_unavailable")
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("cameras"), list):
            self._json_error(502, "camera_inventory_invalid")
            return
        available = self._bridge_available()
        xiaomi = {
            "id": CAMERA_ID,
            "display_name": "Living Room Camera",
            "online": True if available else None,
            "provider": "xiaomi",
            "model": "chuangmi.camera.ipc019",
            "unavailable_reason": None if available else "xiaomi_bridge_unavailable",
            "capabilities": {
                "snapshot": available,
                "live_stream": available,
                "ptz_move": False,
                "ptz_stop": False,
            },
        }
        payload["cameras"] = [camera for camera in payload["cameras"] if camera.get("id") != CAMERA_ID]
        payload["cameras"].append(xiaomi)
        self._send(200, "application/json", json.dumps(payload, separators=(",", ":")).encode())

    def _snapshot(self) -> None:
        query = urllib.parse.urlencode({"src": SNAPSHOT_STREAM_NAME})
        try:
            with self._request(self.go2rtc_url, f"/api/frame.jpeg?{query}") as response:
                content = response.read(MAX_SNAPSHOT_BYTES + 1)
                valid = (
                    response.status == 200
                    and len(content) <= MAX_SNAPSHOT_BYTES
                    and content.startswith(b"\xff\xd8")
                    and content.endswith(b"\xff\xd9")
                )
        except (OSError, socket.timeout, urllib.error.URLError):
            valid = False
            content = b""
        if not valid:
            self._json_error(502, "snapshot_unavailable")
            return
        self._send(200, "image/jpeg", content)

    def _hls_master(self) -> None:
        query = urllib.parse.urlencode({"src": LIVE_STREAM_NAME})
        deadline = time.monotonic() + 15.0
        content = b""
        while time.monotonic() < deadline:
            try:
                with self._request(self.go2rtc_url, f"/api/stream.m3u8?{query}") as response:
                    content = response.read(MAX_PLAYLIST_BYTES + 1)
            except Exception:
                content = b""
            if (
                len(content) <= MAX_PLAYLIST_BYTES
                and content.startswith(b"#EXTM3U")
                and HLS_CHILD.search(content.decode("utf-8", "strict")) is not None
            ):
                break
            time.sleep(0.5)
        else:
            self._json_error(502, "live_stream_unavailable")
            return
        text = content.decode("utf-8", "strict")
        match = HLS_CHILD.search(text)
        if match is None:
            self._json_error(502, "live_stream_unavailable")
            return
        replacement = f"/api/camera-control/{CAMERA_ID}/hls/playlist.m3u8?id={match.group(1)}"
        self._send(200, "application/vnd.apple.mpegurl", HLS_CHILD.sub(replacement, text).encode())

    def _hls_resource(self, path: str, query: str) -> None:
        try:
            values = urllib.parse.parse_qs(query, strict_parsing=True)
        except ValueError:
            self._json_error(404, "not_found")
            return
        session = values.get("id", [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session):
            self._json_error(404, "not_found")
            return
        if path.endswith("playlist.m3u8"):
            upstream_path = f"/api/hls/playlist.m3u8?{urllib.parse.urlencode({'id': session})}"
            limit, content_type = MAX_PLAYLIST_BYTES, "application/vnd.apple.mpegurl"
        else:
            sequence = values.get("n", [""])[0]
            if not sequence.isdigit() or not 0 <= int(sequence) <= 1_000_000:
                self._json_error(404, "not_found")
                return
            upstream_path = f"/api/hls/segment.ts?{urllib.parse.urlencode({'id': session, 'n': sequence})}"
            limit, content_type = MAX_SEGMENT_BYTES, "video/mp2t"
        try:
            with self._request(self.go2rtc_url, upstream_path) as response:
                content = response.read(limit + 1)
        except Exception:
            self._json_error(502, "live_stream_unavailable")
            return
        if response.status != 200 or not content or len(content) > limit:
            self._json_error(502, "live_stream_unavailable")
            return
        if path.endswith("playlist.m3u8"):
            text = content.decode("utf-8", "strict")
            match = HLS_SEGMENT.search(text)
            if match is None or int(match.group(2)) > 1_000_000:
                self._json_error(502, "live_stream_unavailable")
                return
            replacement = (
                f"/api/camera-control/{CAMERA_ID}/hls/segment.ts?"
                f"id={match.group(1)}&n={match.group(2)}"
            )
            content = HLS_SEGMENT.sub(replacement, text).encode()
        self._send(200, content_type, content)

    def _live(self) -> None:
        query = urllib.parse.urlencode({"src": LIVE_STREAM_NAME})
        try:
            upstream = self._request(self.go2rtc_url, f"/api/stream.mp4?{query}")
        except Exception:
            self._json_error(502, "live_stream_unavailable")
            return
        try:
            if upstream.status != 200 or upstream.headers.get_content_type() != "video/mp4":
                self._json_error(502, "live_stream_unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", upstream.headers.get("Content-Type", "video/mp4"))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            while chunk := upstream.read(64 * 1024):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        finally:
            upstream.close()

    def _production_camera(self, path: str) -> None:
        try:
            upstream = self._request(self.production_url, path)
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.headers.get("Content-Type", "application/json"), exc.read(128_001))
            return
        except Exception:
            self._json_error(502, "camera_preview_unavailable")
            return
        try:
            if urllib.parse.urlsplit(path).path.endswith("/live"):
                self.send_response(upstream.status)
                self.send_header("Content-Type", upstream.headers.get("Content-Type", "video/mp4"))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                while chunk := upstream.read(64 * 1024):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                return
            content = upstream.read(MAX_SNAPSHOT_BYTES + 1)
            if len(content) > MAX_SNAPSHOT_BYTES:
                self._json_error(502, "camera_preview_too_large")
                return
            self._send(
                upstream.status,
                upstream.headers.get("Content-Type", "application/octet-stream"),
                content,
            )
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        finally:
            upstream.close()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path in {"/", "/index.html"}:
            self._static_template("index.html", "text/html; charset=utf-8")
            return
        if path in {"/login", "/login.html"}:
            self._static_template("login.html", "text/html; charset=utf-8")
            return
        if path == "/service-worker.js":
            self._static_template("service-worker.js", "application/javascript")
            return
        if path == "/api/auth/status":
            self._auth_proxy()
            return
        camera_prefix = f"/api/camera-control/{CAMERA_ID}"
        if path == "/api/camera-control/devices":
            if self._authenticated():
                self._devices()
            else:
                self._json_error(401, "authentication_required")
            return
        if path.startswith(camera_prefix):
            if not self._authenticated():
                self._json_error(401, "authentication_required")
            elif path == camera_prefix + "/snapshot":
                self._snapshot()
            elif path == camera_prefix + "/live":
                self._live()
            elif path == camera_prefix + "/live.m3u8":
                self._hls_master()
            elif path in {camera_prefix + "/hls/playlist.m3u8", camera_prefix + "/hls/segment.ts"}:
                self._hls_resource(path, parsed.query)
            else:
                self._json_error(404, "not_found")
            return
        if path.startswith("/api/camera-control/"):
            if self._authenticated():
                self._production_camera(self.path)
            else:
                self._json_error(401, "authentication_required")
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urllib.parse.urlsplit(self.path).path in {"/api/auth/login", "/api/auth/logout"}:
            self._auth_proxy(method="POST")
        else:
            self._json_error(405, "read_only_preview")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frontend", type=Path)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--production-url", default="http://127.0.0.1:8090")
    parser.add_argument("--go2rtc-url", default="http://127.0.0.1:2984")
    args = parser.parse_args()
    if not args.frontend.is_dir() or not 1 <= args.port <= 65535:
        raise SystemExit("invalid_preview_configuration")
    PreviewHandler.production_url = loopback_base(args.production_url)
    PreviewHandler.go2rtc_url = loopback_base(args.go2rtc_url)
    server = ThreadingHTTPServer(
        (args.bind, args.port), partial(PreviewHandler, directory=str(args.frontend))
    )
    server.daemon_threads = True
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
