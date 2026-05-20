from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from rover.blynk import get_safety_view_snapshot


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crane Rover Safety</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f1e8;
      --panel: rgba(255, 255, 255, 0.18);
      --border: rgba(255, 255, 255, 0.28);
      --text: #fffdf8;
      --muted: rgba(255, 253, 248, 0.82);
      --pill-bg: rgba(19, 24, 21, 0.22);
      --safe: #246a3f;
      --danger: #a52f2f;
      --neutral: #5b6066;
      --shadow: 0 22px 60px rgba(0, 0, 0, 0.2);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.18), transparent 28rem),
        linear-gradient(160deg, #385c46 0%, #244f66 100%);
      color: var(--text);
      transition: background 180ms ease-in-out;
    }

    body.safe {
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.18), transparent 28rem),
        linear-gradient(160deg, #4e8b45 0%, #1f6146 100%);
    }

    body.danger {
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.16), transparent 28rem),
        linear-gradient(160deg, #c84a4a 0%, #7b1f1f 100%);
    }

    body.stale,
    body.connecting,
    body.unknown {
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.16), transparent 28rem),
        linear-gradient(160deg, #7a8087 0%, #4f555c 100%);
    }

    main {
      min-height: 100vh;
      padding: 1rem;
      display: grid;
      place-items: center;
    }

    .panel {
      width: min(100%, 64rem);
      padding: 1rem;
      border: 1px solid var(--border);
      border-radius: 1.5rem;
      background: var(--panel);
      backdrop-filter: blur(10px);
      box-shadow: var(--shadow);
    }

    .topbar {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1rem;
    }

    .title {
      margin: 0;
      font-size: clamp(1.15rem, 2vw, 1.7rem);
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .subtitle {
      margin: 0.35rem 0 0;
      color: var(--muted);
      font-size: 0.95rem;
    }

    .badge {
      flex-shrink: 0;
      padding: 0.8rem 1rem;
      border-radius: 999px;
      background: var(--pill-bg);
      border: 1px solid rgba(255, 255, 255, 0.24);
      text-align: center;
      min-width: 7rem;
    }

    .badge-label {
      display: block;
      font-size: 0.72rem;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--muted);
    }

    .badge-value {
      display: block;
      margin-top: 0.2rem;
      font-size: 1.4rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    .distance {
      margin: 0;
      font-size: clamp(3rem, 12vw, 7rem);
      line-height: 0.95;
      font-weight: 800;
      letter-spacing: -0.04em;
    }

    .distance-label {
      margin: 0.4rem 0 1.25rem;
      color: var(--muted);
      font-size: clamp(1rem, 2.8vw, 1.2rem);
      text-transform: uppercase;
      letter-spacing: 0.16em;
    }

    .identity-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.85rem;
    }

    .card {
      padding: 1rem;
      border-radius: 1.1rem;
      background: rgba(15, 17, 18, 0.16);
      border: 1px solid rgba(255, 255, 255, 0.16);
    }

    .card-label {
      margin: 0 0 0.35rem;
      font-size: 0.78rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
    }

    .card-value {
      margin: 0;
      font-size: clamp(1.2rem, 4vw, 1.9rem);
      font-weight: 700;
      word-break: break-word;
    }

    .card-mode {
      margin: 0.55rem 0 0;
      font-size: 1rem;
      color: var(--muted);
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .footer {
      margin-top: 1rem;
      color: var(--muted);
      font-size: 0.9rem;
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      flex-wrap: wrap;
    }

    @media (max-width: 640px) {
      .panel { padding: 0.9rem; border-radius: 1.1rem; }
      .topbar { flex-direction: column; align-items: stretch; }
      .badge { align-self: flex-end; }
      .identity-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body class="unknown">
  <main>
    <section class="panel">
      <div class="topbar">
        <div>
          <h1 class="title">Crane Rover Safety</h1>
          <p class="subtitle">Nearest peer safety margin</p>
        </div>
        <div class="badge">
          <span class="badge-label">Status</span>
          <span class="badge-value" id="statusText">Waiting</span>
        </div>
      </div>

      <p class="distance" id="safeDistance">-</p>
      <p class="distance-label">Safe Distance</p>

      <div class="identity-grid">
        <article class="card">
          <p class="card-label">Current Rover</p>
          <p class="card-value" id="roverName">-</p>
          <p class="card-mode" id="roverMode">-</p>
        </article>
        <article class="card">
          <p class="card-label">Nearest Peer</p>
          <p class="card-value" id="peerName">-</p>
          <p class="card-mode" id="peerMode">-</p>
        </article>
      </div>

      <div class="footer">
        <span id="threshold">Threshold: 25.0 m</span>
        <span id="detail">Waiting for peer data</span>
      </div>
    </section>
  </main>

  <script>
    function formatMeters(value) {
      if (typeof value !== "number" || !Number.isFinite(value)) {
        return "-";
      }
      return value.toFixed(2) + " m";
    }

    function localModeText(data) {
      return data.rover_fix_label || "UNKNOWN";
    }

    function peerModeText(data) {
      return data.nearest_peer_fix_label || "UNKNOWN";
    }

    function applyData(data) {
      const state = data.state || "unknown";
      document.body.className = state;
      document.getElementById("statusText").textContent = state === "safe"
        ? "SAFE"
        : state === "danger"
          ? "DANGER"
          : state === "stale"
            ? "STALE DATA"
            : "CONNECTING";

      document.getElementById("safeDistance").textContent = formatMeters(data.safe_distance_m);
      document.getElementById("roverName").textContent = data.rover_name || "-";
      document.getElementById("roverMode").textContent = localModeText(data);
      document.getElementById("peerName").textContent = data.nearest_peer_id || "-";
      document.getElementById("peerMode").textContent = peerModeText(data);
      document.getElementById("threshold").textContent = "Threshold: " + formatMeters(data.threshold_m);

      const parts = [];
      if (typeof data.raw_distance_m === "number" && Number.isFinite(data.raw_distance_m)) {
        parts.push("Raw: " + formatMeters(data.raw_distance_m));
      }
      if (typeof data.nearest_peer_uncertainty_m === "number" && Number.isFinite(data.nearest_peer_uncertainty_m)) {
        parts.push("Uncertainty: " + formatMeters(data.nearest_peer_uncertainty_m));
      }
      if (state === "stale") {
        document.getElementById("detail").textContent = "Stale UDP data";
        return;
      }
      if (state === "connecting") {
        document.getElementById("detail").textContent = "Connecting to peers";
        return;
      }
      document.getElementById("detail").textContent = parts.length ? parts.join(" | ") : "-";
    }

    async function refresh() {
      try {
        const response = await fetch("/api/viewer", { cache: "no-store" });
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        const data = await response.json();
        applyData(data);
      } catch (error) {
        document.body.className = "unknown";
        document.getElementById("statusText").textContent = "CONNECTING";
        document.getElementById("detail").textContent = "Viewer offline";
      }
    }

    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>
"""


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def web_viewer_loop(web_cfg: dict, rover_name: str, stop_event) -> None:
    host = str(web_cfg.get("host", "0.0.0.0"))
    port = int(web_cfg.get("port", 8080))
    threshold_m = float(web_cfg.get("safeDistanceThresholdM", 25.0))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send_html(HTML_PAGE)
                return
            if self.path == "/api/viewer":
                snapshot = get_safety_view_snapshot(rover_name=rover_name, threshold_m=threshold_m)
                payload = {
                    "rover_name": snapshot.rover_name,
                    "rover_fix_label": snapshot.rover_fix_label,
                    "rover_correction_mode": snapshot.rover_correction_mode,
                    "rover_ntrip_connected": snapshot.rover_ntrip_connected,
                    "nearest_peer_id": snapshot.nearest_peer_id,
                    "nearest_peer_fix_label": snapshot.nearest_peer_fix_label,
                    "safe_distance_m": snapshot.safe_distance_m,
                    "raw_distance_m": snapshot.raw_distance_m,
                    "nearest_peer_accuracy_m": snapshot.nearest_peer_accuracy_m,
                    "nearest_peer_uncertainty_m": snapshot.nearest_peer_uncertainty_m,
                    "threshold_m": snapshot.threshold_m,
                    "state": snapshot.state,
                    "peer_count": snapshot.peer_count,
                    "fresh_peer_count": snapshot.fresh_peer_count,
                    "updated_at": snapshot.updated_at,
                }
                self._send_json(payload)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, fmt: str, *args) -> None:
            logging.debug("Web viewer %s - %s", self.address_string(), fmt % args)

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: dict) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

    server = _ThreadingHTTPServer((host, port), Handler)
    server.timeout = 0.5
    logging.info("Web viewer started at http://%s:%s", host, port)

    try:
        while not stop_event.is_set():
            server.handle_request()
    except Exception as exc:
        logging.error("Web viewer error: %s", exc)
    finally:
        server.server_close()
        logging.info("Web viewer stopped")
