"""Live MJPEG stream of the bot's HUD view, shown inside the dashboard's Worker viewport.

The controller already renders the HUD (red zombie overlays, 360 radar, action banner) every other frame;
publish() hands that image to a tiny local HTTP server, so the game itself can stay off-screen:

    http://127.0.0.1:8766/stream.mjpg   multipart JPEG stream (~10-15 fps)
    http://127.0.0.1:8766/frame.jpg     latest frame
    http://127.0.0.1:8766/health        {"live": true/false, "fps": ..., "age_s": ...}

Local only (127.0.0.1). Start with `python -m bot.worker run ... --stream`.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

_lock = threading.Condition()
_jpeg = None          # HUD view (red overlays, radar, action)
_raw = None           # the plain game frame
_seq = 0
_t_last = 0.0
_fps = 0.0
_server = None


def active():
    return _server is not None


def publish(img_bgr, max_w=960, quality=72, raw_bgr=None):
    """Encode and share one HUD frame (and optionally the raw game frame). No-op when the stream isn't running."""
    global _jpeg, _raw, _seq, _t_last, _fps
    if _server is None or img_bgr is None:
        return
    if raw_bgr is not None:
        rh, rw = raw_bgr.shape[:2]
        small = cv2.resize(raw_bgr, (max_w, int(rh * max_w / rw)), interpolation=cv2.INTER_AREA) if rw > max_w else raw_bgr
        ok_r, enc_r = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok_r:
            _raw = enc_r.tobytes()
    h, w = img_bgr.shape[:2]
    if w > max_w:
        img_bgr = cv2.resize(img_bgr, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
    ok, enc = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return
    now = time.time()
    with _lock:
        if _t_last:
            _fps = 0.9 * _fps + 0.1 / max(now - _t_last, 1e-3)
        _jpeg, _seq, _t_last = enc.tobytes(), _seq + 1, now
        _lock.notify_all()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _head(self, ctype, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")  # the dashboard on :8765 checks /health
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            with _lock:
                age = time.time() - _t_last if _t_last else None
                body = {"live": _jpeg is not None and age is not None and age < 3.0,
                        "fps": round(_fps, 1), "age_s": round(age, 2) if age is not None else None}
            self._head("application/json")
            self.wfile.write(json.dumps(body).encode())
        elif path == "/frame.jpg":
            with _lock:
                data = _jpeg
            if data is None:
                self._head("text/plain", 204)
                return
            self._head("image/jpeg")
            self.wfile.write(data)
        elif path == "/stream.mjpg":
            raw = "view=raw" in self.path  # ?view=raw: the plain game frame instead of the HUD view
            self._head("multipart/x-mixed-replace; boundary=frame")
            seen = -1
            try:
                while True:
                    with _lock:
                        _lock.wait_for(lambda: _seq != seen, timeout=5.0)
                        data, seen = (_raw if raw else _jpeg), _seq
                    if data is None:
                        continue
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return  # viewer closed the tab
        else:
            self._head("text/plain", 404)


def url():
    """Stream URL advertised to the dashboard. STREAM_PUBLIC_URL (e.g. a Cloudflare tunnel to :8766) lets a
    hosted dashboard reach it; otherwise it's the local address."""
    import os
    public = os.environ.get("STREAM_PUBLIC_URL", "").strip().rstrip("/")
    if public:
        return f"{public}/stream.mjpg"
    return f"http://127.0.0.1:{_server.server_address[1]}/stream.mjpg" if _server else None


def start(port=8766):
    """Start the stream server in a background thread (idempotent)."""
    global _server
    if _server is None:
        _server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        _server.daemon_threads = True
        threading.Thread(target=_server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/stream.mjpg"
