"""
FL Studio MCP Server — Anthropic-API compatible.

Exposes the FL Studio Bridge as native MCP tools so any Opus 5.5 API client
(simple HTTP, no MCP protocol required from the model) can drive FL Studio.

Run:
    uv run --python 3.12 --directory "C:\\Users\\4l13n\\Desktop\\fl-studio-mcp\\bridge" fl_mcp.py

Or via the launcher:
    start_bridge.bat  (already configured to launch this instead of fl_bridge.py)
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import urllib.request
import urllib.error

BRIDGE_URL = os.environ.get("FL_BRIDGE_URL", "http://localhost:8765")
PORT = int(os.environ.get("FL_MCP_PORT", "8766"))


def bridge_call(method: str, path: str, payload: dict | None = None) -> dict:
    """POST/GET to the FL Studio Bridge and return parsed JSON."""
    url = f"{BRIDGE_URL}{path}"
    if method == "GET":
        req = urllib.request.Request(url, method="GET")
    else:
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"bridge unreachable: {exc.reason}"}


# ---------------------------------------------------------------------------
# Tool catalog — these get exposed to the Opus 5.5 API client
# ---------------------------------------------------------------------------
TOOLS = [
    {"name": "fl_transport_play",      "description": "Start FL Studio playback.",        "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "fl_transport_stop",      "description": "Stop FL Studio playback.",         "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "fl_transport_record",    "description": "Toggle record on/off.",            "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "fl_transport_bpm",       "description": "Set project BPM.",                 "input_schema": {"type": "object", "properties": {"bpm": {"type": "integer", "minimum": 20, "maximum": 999}}, "required": ["bpm"]}},
    {"name": "fl_mixer_volume",        "description": "Set channel volume (0.0 to 1.0).",  "input_schema": {"type": "object", "properties": {"channel": {"type": "integer"}, "value": {"type": "number"}}, "required": ["channel", "value"]}},
    {"name": "fl_mixer_pan",           "description": "Set channel pan (-1.0 to 1.0).",   "input_schema": {"type": "object", "properties": {"channel": {"type": "integer"}, "value": {"type": "number"}}, "required": ["channel", "value"]}},
    {"name": "fl_mixer_mute",          "description": "Mute / unmute a channel.",         "input_schema": {"type": "object", "properties": {"channel": {"type": "integer"}, "on": {"type": "boolean"}}, "required": ["channel"]}},
    {"name": "fl_mixer_solo",          "description": "Solo / unsolo a channel.",         "input_schema": {"type": "object", "properties": {"channel": {"type": "integer"}, "on": {"type": "boolean"}}, "required": ["channel"]}},
    {"name": "fl_piano_clear",         "description": "Clear notes in channel.",          "input_schema": {"type": "object", "properties": {"channel": {"type": "integer"}}, "required": ["channel"]}},
    {"name": "fl_piano_add_chord",     "description": "Insert a chord at given beat.",   "input_schema": {"type": "object", "properties": {"root": {"type": "string"}, "quality": {"type": "string"}, "octave": {"type": "integer"}, "length": {"type": "number"}, "velocity": {"type": "integer"}, "channel": {"type": "integer"}, "start": {"type": "number"}}, "required": ["root"]}},
    {"name": "fl_piano_arp",           "description": "Insert an arpeggio.",              "input_schema": {"type": "object", "properties": {"root": {"type": "string"}, "quality": {"type": "string"}, "octaves": {"type": "integer"}, "steps": {"type": "array", "items": {"type": "integer"}}, "rate": {"type": "number"}, "channel": {"type": "integer"}, "start": {"type": "number"}}, "required": ["root"]}},
    {"name": "fl_project_open",        "description": "Load a .flp file from disk (offline mode).", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "fl_project_new",         "description": "Create a blank project in memory (offline mode).", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": []}},
    {"name": "fl_project_save",        "description": "Save current project as .flp", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}},
    {"name": "fl_project_export_midi", "description": "Export current project as MIDI (.mid) — works with FL Studio File > Import MIDI", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}},
    {"name": "fl_project_inspect",     "description": "Inspect any .flp file without opening it.",  "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "fl_get_state",           "description": "Snapshot of FL Studio state.",     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "fl_get_health",          "description": "Liveness probe.",                  "input_schema": {"type": "object", "properties": {}, "required": []}},
]


def run_tool(name: str, args: dict) -> dict:
    """Dispatch a tool call to the local Bridge."""
    table = {
        "fl_transport_play":   ("POST", "/transport/play"),
        "fl_transport_stop":   ("POST", "/transport/stop"),
        "fl_transport_record": ("POST", "/transport/record"),
        "fl_transport_bpm":    ("POST", "/transport/bpm"),
        "fl_mixer_volume":     ("POST", "/mixer/volume"),
        "fl_mixer_pan":        ("POST", "/mixer/pan"),
        "fl_mixer_mute":       ("POST", "/mixer/mute"),
        "fl_mixer_solo":       ("POST", "/mixer/solo"),
        "fl_piano_clear":      ("POST", "/piano/clear"),
        "fl_piano_add_chord":  ("POST", "/piano/add_chord"),
        "fl_piano_arp":        ("POST", "/piano/arp"),
        "fl_project_open":     ("POST", "/project/open"),
        "fl_project_new":      ("POST", "/project/new"),
        "fl_project_save":     ("POST", "/project/save"),
        "fl_project_export_midi": ("POST", "/project/export_midi"),
        "fl_get_state":        ("GET",  "/state"),
        "fl_get_health":       ("GET",  "/health"),
    }
    if name == "fl_project_inspect":
        path = args.get("path", "")
        if not path:
            return {"ok": False, "error": "missing 'path' argument"}
        from urllib.parse import quote
        return bridge_call("GET", f"/project/inspect?path={quote(path)}", None)
    if name not in table:
        return {"ok": False, "error": f"unknown tool {name}"}
    method, path = table[name]
    return bridge_call(method, path, args)


# ---------------------------------------------------------------------------
# Tiny HTTP server exposing /tools (catalog) and /call (invoke)
# ---------------------------------------------------------------------------
class MCPHandler(BaseHTTPRequestHandler):
    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def log_message(self, fmt, *args):
        sys.stderr.write("[fl-mcp] " + (fmt % args) + "\n")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/tools":
            return self._json(200, {"ok": True, "tools": TOOLS, "bridge_url": BRIDGE_URL})
        if path == "/health":
            return self._json(200, {"ok": True, "tools_count": len(TOOLS)})
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()
        if path == "/call":
            name = body.get("name", "")
            args = body.get("arguments", {}) or {}
            result = run_tool(name, args)
            return self._json(200, {"ok": True, "result": result})
        self._json(404, {"ok": False, "error": "not found"})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), MCPHandler)
    print(f"[fl-mcp] Tool gateway listening on http://localhost:{PORT}")
    print(f"[fl-mcp] Bridge target: {BRIDGE_URL}")
    print(f"[fl-mcp] Tools: {len(TOOLS)}")
    print(f"[fl-mcp] GET  /tools  -> catalog")
    print('[fl-mcp] POST /call   -> {"name": "...", "arguments": {...}}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()