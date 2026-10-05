"""
FL Studio Bridge - REST HTTP server with offline (pyflp) + live (MIDI) backends.

Talks to FL Studio in two ways:
  1. Offline (pyflp)  -- reads/writes .flp project files on disk
  2. Live (MIDI)      -- sends trigger events to the controller script running
                         inside FL Studio via loopMIDI virtual port

Endpoints:
  GET  /health                       liveness probe
  GET  /state                        snapshot of currently loaded project
  GET  /project/inspect?path=...      read-only inspection of any .flp
  POST /project/open     {path}      load a .flp into memory
  POST /project/save     {path?}     save current project
  POST /transport/play|stop|record
  POST /transport/bpm    {bpm}
  POST /mixer/volume|pan|mute|solo  {channel, value|on}
  POST /piano/clear      {channel}
  POST /piano/add_chord  {root, quality, octave, length, velocity, channel, start}
  POST /piano/arp        {root, quality, octaves, steps, rate, channel, start}

Live MIDI calls fall back to offline pyflp automatically when FL isn't responding.
"""

from __future__ import annotations

import json
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# Offline AST backend
from fl_pyflp_backend import (  # type: ignore
    HAS_PYFLP,
    open_project as pyflp_open,
    create_blank_project as pyflp_create_blank,
    get_state as pyflp_state,
    set_bpm as pyflp_set_bpm,
    set_mixer as pyflp_set_mixer,
    add_chord_to_channel as pyflp_add_chord,
    clear_channel as pyflp_clear,
    save as pyflp_save,
    inspect as pyflp_inspect,
)

# Live MIDI bus
try:
    import mido  # type: ignore
    HAS_MIDO = True
except Exception:
    mido = None  # type: ignore
    HAS_MIDO = False

PORT = int(os.environ.get("FL_BRIDGE_PORT", "8767"))
MIDI_PORT_NAME = "FLStudioMCP"
COMMAND_FILE = os.path.join(os.environ.get("TEMP", "/tmp"), "mcp_command.json")
RESPONSE_FILE = os.path.join(os.environ.get("TEMP", "/tmp"), "mcp_response.json")


def _resolve_midi_port() -> str | None:
    if not HAS_MIDO:
        return None
    for name in mido.get_output_names():
        if name.split(" ")[0] == MIDI_PORT_NAME:
            return name
    return None


# Live MIDI state
_midi_lock = threading.Lock()
_midi_out = None


def midi_send(payload: dict, timeout: float = 5.0) -> dict:
    """Try live MIDI; returns a dict with ok=False on any failure (timeout, no port, etc.)."""
    global _midi_out
    if not HAS_MIDO:
        return {"ok": False, "error": "mido not available"}
    if _midi_out is None:
        port = _resolve_midi_port()
        if port is None:
            return {"ok": False, "error": f"midi port '{MIDI_PORT_NAME}*' not found"}
        try:
            _midi_out = mido.open_output(port)
        except Exception as exc:
            return {"ok": False, "error": f"midi open failed: {exc}"}

    try:
        with _midi_lock:
            with open(COMMAND_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            _midi_out.send(mido.Message("note_on", note=127, velocity=127, channel=0))
            time.sleep(0.02)
            _midi_out.send(mido.Message("note_off", note=127, velocity=0, channel=0))
            deadline = time.time() + timeout
            while time.time() < deadline:
                if os.path.exists(RESPONSE_FILE):
                    try:
                        with open(RESPONSE_FILE, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        os.remove(RESPONSE_FILE)
                        return data
                    except Exception:
                        pass
                time.sleep(0.02)
        return {"ok": False, "error": "timeout waiting for FL Studio response"}
    except Exception as exc:
        return {"ok": False, "error": f"midi send failed: {exc}"}


# ---------------------------------------------------------------------------
# Harmonic helpers
# ---------------------------------------------------------------------------
SCALES = {
    "major":      [0, 2, 4, 5, 7, 9, 11],
    "minor":      [0, 2, 3, 5, 7, 8, 10],
    "dorian":     [0, 2, 3, 5, 7, 9, 10],
    "phrygian":   [0, 1, 3, 5, 7, 8, 10],
    "lydian":     [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "harmonic":   [0, 2, 3, 5, 7, 8, 11],
    "melodic":    [0, 2, 3, 5, 7, 9, 11],
    "blues":      [0, 3, 5, 6, 7, 10],
    "pentatonic": [0, 3, 5, 7, 10],
}

CHORD_QUALITIES = {
    "":      [0, 4, 7],
    "m":     [0, 3, 7],
    "dim":   [0, 3, 6],
    "aug":   [0, 4, 8],
    "sus4":  [0, 5, 7],
    "sus2":  [0, 2, 7],
    "7":     [0, 4, 7, 10],
    "maj7":  [0, 4, 7, 11],
    "m7":    [0, 3, 7, 10],
    "m7b5":  [0, 3, 6, 10],
    "dim7":  [0, 3, 6, 9],
    "9":     [0, 4, 7, 10, 14],
    "add9":  [0, 4, 7, 14],
    "6":     [0, 4, 7, 9],
    "m6":    [0, 3, 7, 9],
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_to_midi(note: str, octave: int) -> int:
    return NOTE_NAMES.index(note.upper()) + (octave + 1) * 12


def build_chord(root: str, quality: str, octave: int) -> list[int]:
    intervals = CHORD_QUALITIES.get(quality, CHORD_QUALITIES[""])
    base = note_to_midi(root, octave)
    return [base + i for i in intervals]


def build_arp(root: str, quality: str, octaves: int, steps: list[int]) -> list[int]:
    notes = []
    for oct_shift in range(octaves):
        base = note_to_midi(root, octave=3 + oct_shift)
        for step in steps:
            ivs = CHORD_QUALITIES[quality]
            notes.append(base + (ivs[step % len(ivs)] if step < 4 else 12))
    return notes


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class BridgeHandler(BaseHTTPRequestHandler):
    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body, indent=2, default=str).encode("utf-8")
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
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, fmt, *args):
        sys.stderr.write("[bridge] " + (fmt % args) + "\n")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # -----------------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._json(200, {
                "ok": True,
                "pyflp": HAS_PYFLP,
                "mido": HAS_MIDO,
                "midi_port": MIDI_PORT_NAME,
                "ts": time.time(),
                "mode": "offline+live (auto fallback)",
            })
        if path == "/state":
            return self._json(200, pyflp_state())
        if path == "/project/inspect":
            from urllib.parse import unquote
            qs = urlparse(self.path).query
            params = {}
            if qs:
                for pair in qs.split("&"):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        params[k] = unquote(v)
            target = params.get("path", "")
            if not target:
                return self._json(400, {"ok": False, "error": "missing ?path=..."})
            return self._json(200, pyflp_inspect(target))
        self._json(404, {"ok": False, "error": "not found"})

    # -----------------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()

        # ---- project (offline only) ---------------------------------------
        if path == "/project/open":
            return self._json(200, pyflp_open(body.get("path", "")))
        if path == "/project/save":
            return self._json(200, pyflp_save(body.get("path")))
        if path == "/project/new":
            return self._json(200, pyflp_create_blank(body.get("title", "AI Composed")))

        # ---- transport (controller script uses 'transport.start/stop/record') ---
        if path in ("/transport/play", "/transport/stop", "/transport/record"):
            action_map = {"play": "start", "stop": "stop", "record": "record"}
            requested = path.rsplit("/", 1)[-1]
            action = action_map[requested]
            live = midi_send({"cmd": "transport", "action": action})
            if live.get("ok"):
                return self._json(200, live)
            return self._json(200, {"ok": True, "mode": "offline",
                                      "note": f"FL not responding; queued '{action}'"})

        if path == "/transport/bpm":
            bpm = int(body.get("bpm", 120))
            live = midi_send({"cmd": "transport", "action": "set_bpm", "bpm": bpm})
            if live.get("ok"):
                return self._json(200, live)
            off = pyflp_set_bpm(bpm)
            if off.get("ok"):
                off["mode"] = "offline"
            return self._json(200, off)

        # ---- mixer (controller uses channels.* not mixer.*) -----------------
        if path == "/mixer/volume":
            ch = int(body.get("channel", 0))
            val = float(body.get("value", 0.8))
            live = midi_send({"cmd": "channels", "action": "setVolume", "index": ch, "volume": val})
            if live.get("ok"):
                return self._json(200, live)
            return self._json(200, pyflp_set_mixer(ch, volume=val))
        if path == "/mixer/pan":
            ch = int(body.get("channel", 0))
            val = float(body.get("value", 0.0))
            live = midi_send({"cmd": "channels", "action": "setPan", "index": ch, "pan": val})
            if live.get("ok"):
                return self._json(200, live)
            return self._json(200, pyflp_set_mixer(ch, pan=val))
        if path == "/mixer/mute":
            ch = int(body.get("channel", 0))
            on = bool(body.get("on", True))
            live = midi_send({"cmd": "channels", "action": "setMute", "index": ch, "mute": on})
            if live.get("ok"):
                return self._json(200, live)
            return self._json(200, pyflp_set_mixer(ch, mute=on))
        if path == "/mixer/solo":
            ch = int(body.get("channel", 0))
            on = bool(body.get("on", True))
            live = midi_send({"cmd": "channels", "action": "setSolo", "index": ch, "solo": on})
            if live.get("ok"):
                return self._json(200, live)
            return self._json(200, pyflp_set_mixer(ch, solo=on))

        # ---- piano roll (controller has no note API, fallback to offline) ----
        if path == "/piano/clear":
            ch = int(body.get("channel", 0))
            return self._json(200, pyflp_clear(ch))

        if path == "/piano/add_chord":
            root = body.get("root", "C")
            quality = body.get("quality", "")
            octave = int(body.get("octave", 4))
            length = float(body.get("length", 1.0))
            velocity = int(body.get("velocity", 100))
            channel = int(body.get("channel", 0))
            start = float(body.get("start", 0.0))
            notes = build_chord(root, quality, octave)
            return self._json(200, pyflp_add_chord(channel, notes, length, velocity, start))

        if path == "/piano/arp":
            root = body.get("root", "C")
            quality = body.get("quality", "m7")
            octaves = int(body.get("octaves", 2))
            steps = body.get("steps", [0, 2, 1, 3])
            rate = float(body.get("rate", 0.25))
            channel = int(body.get("channel", 0))
            start = float(body.get("start", 0.0))
            notes = build_arp(root, quality, octaves, steps)
            inserted = []
            for i, n in enumerate(notes):
                inserted.append(pyflp_add_chord(channel, [n], rate, 95, start + i * rate))
            return self._json(200, {"ok": True, "mode": "offline",
                                     "notes": len(notes), "results": inserted})

        self._json(404, {"ok": False, "error": f"unknown route {path}"})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), BridgeHandler)
    print(f"[bridge] FL Studio Bridge listening on http://localhost:{PORT}")
    print(f"[bridge] pyflp: {HAS_PYFLP}  mido: {HAS_MIDO}")
    print(f"[bridge] Endpoints: /health /project/open /project/save /project/inspect /state")
    print(f"[bridge]            /transport/{'{play,stop,record,bpm}'}")
    print(f"[bridge]            /mixer/{'{volume,pan,mute,solo}'}")
    print(f"[bridge]            /piano/{'{clear,add_chord,arp}'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()