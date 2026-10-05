"""
FL Studio offline backend using a custom FLP writer (avoids pyflp 2.2.1 bug).

The pyflp library fails to parse FL Studio 26 .flp files because EventEnum
has no members for the new format. This module works around it by:
  - Tracking project state in Python (channels, patterns, notes)
  - Writing .flp files using our minimal FLPWriter
  - Reading .flp files with our FLP minimal parser as best-effort
"""

from __future__ import annotations
import os
import struct
import threading
from pathlib import Path
from typing import Any

from fl_writer import FLPWriter  # type: ignore

# Lock for the in-memory project
_project_lock = threading.RLock()

# Active project state (in-memory)
_state: dict = {
    "loaded": False,
    "path": None,
    "name": "AI Composed",
    "bpm": 120.0,
    "ppq": 96,
    "channels": [],         # [{index, name, volume, pan, mute, solo}, ...]
    "patterns": [],         # [{name, notes: [{channel, pitch, start, length, velocity}]}, ...]
    "writer": None,         # FLPWriter instance
}

# ---------------------------------------------------------------------------
# Project lifecycle
# ---------------------------------------------------------------------------
def new_project(title: str = "AI Composed", bpm: float = 120.0) -> dict:
    with _project_lock:
        _state["loaded"] = True
        _state["path"] = None
        _state["name"] = title
        _state["bpm"] = float(bpm)
        _state["ppq"] = 96
        _state["channels"] = []
        _state["patterns"] = [{"name": "AI Composed", "notes": []}]
        _state["writer"] = FLPWriter(title=title, bpm=float(bpm), ppq=96)
        return {
            "ok": True,
            "name": title,
            "bpm": float(bpm),
            "channels": 0,
            "patterns": 1,
            "note": "in-memory project created; use /project/save to persist",
        }


def ensure_project() -> bool:
    """Lazy-create a project if none loaded."""
    if _state["loaded"]:
        return True
    new_project()
    return True


def open_project(path: str) -> dict:
    """Open an existing .flp. Note: limited to header inspection; full FLP
    read still requires pyflp. We just record the path and treat channels
    as discovered by the live MIDI controller."""
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"file not found: {p}"}
    with _project_lock:
        # Inspect just the header (FLhd) for metadata
        try:
            data = p.read_bytes()
            if not data.startswith(b"FLhd"):
                return {"ok": False, "error": "not a valid FLP (missing FLhd)"}
            fl_type = struct.unpack_from("<H", data, 4)[0]
            ppq = struct.unpack_from("<H", data, 10)[0]
            size = len(data)
        except Exception as exc:
            return {"ok": False, "error": f"failed to read: {exc}"}

        _state["loaded"] = True
        _state["path"] = str(p)
        _state["name"] = p.stem
        _state["ppq"] = ppq
        _state["bpm"] = 120.0
        _state["channels"] = []
        _state["patterns"] = []
        # Build a writer for future saves
        _state["writer"] = FLPWriter(title=_state["name"], bpm=120.0, ppq=ppq)
        return {
            "ok": True,
            "path": str(p),
            "name": _state["name"],
            "fl_type": fl_type,
            "ppq": ppq,
            "size_bytes": size,
            "note": "FLP opened; use /mixer/* and /piano/* to edit (full content "
                    "editing requires FL Studio to re-save the project)",
        }


def get_state() -> dict:
    with _project_lock:
        if not _state["loaded"]:
            return {"ok": True, "loaded": False, "note": "no project; call /project/new"}
        return {
            "ok": True,
            "loaded": True,
            "path": _state["path"],
            "name": _state["name"],
            "bpm": _state["bpm"],
            "ppq": _state["ppq"],
            "channels": [c for c in _state["channels"]],
            "patterns": [{"name": p["name"], "notes": len(p["notes"])} for p in _state["patterns"]],
        }


def save(path: str | None = None) -> dict:
    target = path or _state["path"]
    if not target:
        return {"ok": False, "error": "no destination path; pass 'path' or open a project first"}
    with _project_lock:
        if _state["writer"] is None:
            return {"ok": False, "error": "no project loaded"}
        try:
            w: FLPWriter = _state["writer"]
            for ch in _state["channels"]:
                w.add_channel(ch["name"], ch["volume"], ch["pan"], ch["mute"], ch["solo"])
            for pat in _state["patterns"]:
                if not pat["notes"]:
                    continue
                w.add_pattern(pat["name"])
                # Group notes by channel into a single event
                for ch_idx, channel_notes in _group_by(pat["notes"], "channel").items():
                    w.add_notes(ch_idx, [(n["pitch"], n["start"], n["length"], n["velocity"])
                                          for n in channel_notes])
            w.save(target)
            _state["path"] = target
            return {"ok": True, "saved_to": target, "size_bytes": os.path.getsize(target)}
        except Exception as exc:
            return {"ok": False, "error": f"save failed: {exc}"}


def inspect(path: str) -> dict:
    """Read-only header inspection."""
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"file not found: {p}"}
    try:
        data = p.read_bytes()
        if not data.startswith(b"FLhd"):
            return {"ok": False, "error": "not a valid FLP"}
        fl_type = struct.unpack_from("<H", data, 4)[0]
        ppq = struct.unpack_from("<H", data, 10)[0]
        # Find title (event ID 195 = Title)
        title = None
        tempo = None
        pos = data.find(b"FLst")
        if pos > 0:
            pos += 4
            while pos < len(data) - 2:
                evt = data[pos]
                size = data[pos + 1]
                pos += 2
                if size & 0x80:
                    size = struct.unpack_from("<I", data, pos)[0] & 0x7FFFFFFF
                    pos += 4
                if pos + size > len(data):
                    break
                payload = data[pos:pos+size]
                pos += size
                if evt == 195 and size > 0:  # Title
                    title = payload.decode("windows-1252", errors="replace").rstrip("\x00")
                elif evt == 66 and size >= 2:  # TempoCoarse
                    tempo = float(struct.unpack_from("<H", payload, 0)[0])
        return {
            "ok": True,
            "path": str(p),
            "title": title,
            "tempo_bpm": tempo,
            "ppq": ppq,
            "fl_type": fl_type,
            "size_bytes": len(data),
        }
    except Exception as exc:
        return {"ok": False, "error": f"inspect failed: {exc}"}


# ---------------------------------------------------------------------------
# Transport / mixer / piano operations
# ---------------------------------------------------------------------------
def set_bpm(bpm: int) -> dict:
    ensure_project()
    with _project_lock:
        _state["bpm"] = float(bpm)
        if _state["writer"]:
            _state["writer"].bpm = float(bpm)
        return {"ok": True, "bpm": float(bpm)}


def set_mixer(channel: int, **kwargs) -> dict:
    ensure_project()
    with _project_lock:
        # Ensure channel exists
        while len(_state["channels"]) <= channel:
            idx = len(_state["channels"])
            _state["channels"].append({
                "index": idx, "name": f"Channel {idx+1}",
                "volume": 0.8, "pan": 0.0, "mute": False, "solo": False,
            })
        ch = _state["channels"][channel]
        if "volume" in kwargs:
            ch["volume"] = max(0.0, min(1.0, float(kwargs["volume"])))
        if "pan" in kwargs:
            ch["pan"] = max(-1.0, min(1.0, float(kwargs["pan"])))
        if "mute" in kwargs:
            ch["mute"] = bool(kwargs["mute"])
        if "solo" in kwargs:
            ch["solo"] = bool(kwargs["solo"])
        return {"ok": True, "channel": channel, "applied": {k: ch[k] for k in kwargs}}


def add_chord_to_channel(channel: int, notes: list[int], length: float,
                          velocity: int, start: float) -> dict:
    ensure_project()
    with _project_lock:
        # Ensure channel exists in mixer
        while len(_state["channels"]) <= channel:
            set_mixer(channel)
        # Ensure pattern exists
        if not _state["patterns"]:
            _state["patterns"].append({"name": "AI Composed", "notes": []})
        pattern = _state["patterns"][-1]
        for pitch in notes:
            pattern["notes"].append({
                "channel": channel,
                "pitch": int(pitch),
                "start": float(start),
                "length": float(length),
                "velocity": int(velocity),
            })
        return {
            "ok": True,
            "pattern": pattern["name"],
            "notes_added": len(notes),
            "channel": channel,
            "start_beat": start,
            "total_notes": sum(len(p["notes"]) for p in _state["patterns"]),
        }


def clear_channel(channel: int) -> dict:
    ensure_project()
    with _project_lock:
        removed = 0
        for pat in _state["patterns"]:
            before = len(pat["notes"])
            pat["notes"] = [n for n in pat["notes"] if n["channel"] != channel]
            removed += before - len(pat["notes"])
        return {"ok": True, "channel": channel, "notes_removed": removed}


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------
def _group_by(items: list[dict], key: str) -> dict[Any, list]:
    out: dict = {}
    for it in items:
        out.setdefault(it[key], []).append(it)
    return out