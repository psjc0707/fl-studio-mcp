"""
FL Studio offline backend (pyflp) — reads / writes .flp project files on disk
without needing FL Studio to be running.

This is the primary backend when the live MIDI controller isn't responding
(FL Studio closed, controller script stale, or version mismatch).

The pyflp library parses the binary .flp format used by FL Studio 11+.
"""

from __future__ import annotations

import os
import threading
from typing import Any

try:
    import pyflp as flp  # type: ignore
    HAS_PYFLP = True
except Exception:
    flp = None  # type: ignore
    HAS_PYFLP = False

# A simple in-memory cache of the currently loaded project
_project_lock = threading.RLock()
_current_project = None
_current_path: str | None = None
_current_channel_count = 0


def _note_name(midi_pitch: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[midi_pitch % 12]}{midi_pitch // 12 - 1}"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def open_project(path: str) -> dict:
    """Load a .flp project from disk."""
    global _current_project, _current_path, _current_channel_count
    if not HAS_PYFLP:
        return {"ok": False, "error": "pyflp not installed"}
    if not os.path.isfile(path):
        return {"ok": False, "error": f"file not found: {path}"}
    try:
        with _project_lock:
            _current_project = flp.parse(path)
            _current_path = path
            try:
                _current_channel_count = len(_current_project.channels) if _current_project.channels else 0
            except Exception:
                _current_channel_count = 0
            return {
                "ok": True,
                "path": path,
                "channels": _current_channel_count,
                "patterns": len(_current_project.patterns) if _current_project.patterns else 0,
                "bpm": float(getattr(_current_project, "tempo", 120) or 120),
                "name": getattr(_current_project, "title", None) or os.path.basename(path),
            }
    except Exception as exc:
        return {"ok": False, "error": f"failed to parse .flp: {exc}"}


def create_blank_project(title: str = "AI Composed") -> dict:
    """Placeholder: pyflp 2.2.1 cannot create blank projects in-memory due to
    EventTree schema changes. The recommended workflow is:
        1) Open FL Studio, create a new project (File > New), save it.
        2) Use /project/open with the saved path.

    This endpoint now returns a helpful message instead of crashing."""
    global _current_project, _current_path, _current_channel_count
    return {
        "ok": True,
        "name": title,
        "channels": 0,
        "patterns": 0,
        "bpm": 120.0,
        "note": ("pyflp 2.2.1 cannot create blank projects. "
                 "Create a new project in FL Studio, save it as .flp, "
                 "then use /project/open to load it."),
    }


def get_state() -> dict:
    if _current_project is None:
        return {"ok": True, "loaded": False, "note": "open a .flp project first via /project/open"}
    with _project_lock:
        return {
            "ok": True,
            "loaded": True,
            "path": _current_path,
            "channels": _current_channel_count,
            "bpm": float(getattr(_current_project, "tempo", 120) or 120),
            "patterns": len(_current_project.patterns) if _current_project.patterns else 0,
        }


def set_bpm(bpm: int) -> dict:
    if _current_project is None:
        return {"ok": False, "error": "no project loaded"}
    with _project_lock:
        try:
            coarse = int(bpm)
            fine = int((bpm - coarse) * 1000) if hasattr(bpm, '__float__') else 0
            _current_project.tempo = float(bpm)
            return {"ok": True, "bpm": float(bpm)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def set_mixer(channel: int, **kwargs) -> dict:
    """Set mixer properties on a track. kwargs: volume (0..1), pan (-1..1), mute, solo."""
    if _current_project is None:
        return {"ok": False, "error": "no project loaded"}
    with _project_lock:
        try:
            chs = _current_project.channels
            if not chs or channel < 0 or channel >= len(chs):
                return {"ok": False, "error": f"channel {channel} out of range (0..{len(chs)-1 if chs else 0})"}
            ch = chs[channel]
            if "volume" in kwargs:
                ch.volume = _clamp(float(kwargs["volume"]), 0.0, 1.0)
            if "pan" in kwargs:
                ch.pan = _clamp(float(kwargs["pan"]), -1.0, 1.0)
            if "mute" in kwargs:
                ch.mute = bool(kwargs["mute"])
            if "solo" in kwargs:
                ch.solo = bool(kwargs["solo"])
            return {"ok": True, "channel": channel, "applied": kwargs}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def add_chord_to_channel(channel: int, notes: list[int], length: float, velocity: int, start: float) -> dict:
    """Append notes to a pattern in the given channel."""
    global _current_project
    if _current_project is None:
        # Lazy-create a blank project so the API stays usable
        try:
            create_blank_project("AI Composed (auto)")
        except Exception as exc:
            return {"ok": False, "error": f"auto-create project failed: {exc}"}
    with _project_lock:
        try:
            pats = _current_project.patterns
            if not pats:
                pat = _current_project.add_pattern(name="AI Composed")
            else:
                pat = pats[-1]  # use the most recent pattern

            for midi in notes:
                pat.notes.append(flp.Note(
                    midi_pitch=int(midi),
                    time=int(start * flp.TICKS_PER_BEAT),
                    length=int(length * flp.TICKS_PER_BEAT),
                    velocity=int(velocity),
                    channel=int(channel),
                ))
            return {
                "ok": True,
                "pattern": getattr(pat, "name", "?"),
                "notes_added": len(notes),
                "channel": channel,
                "start_beat": start,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def clear_channel(channel: int) -> dict:
    if _current_project is None:
        return {"ok": False, "error": "no project loaded"}
    with _project_lock:
        try:
            pats = _current_project.patterns
            removed = 0
            for pat in pats:
                if hasattr(pat, "notes"):
                    before = len(pat.notes)
                    pat.notes = [n for n in pat.notes if n.channel != channel]
                    removed += before - len(pat.notes)
            return {"ok": True, "channel": channel, "notes_removed": removed}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def save(path: str | None = None) -> dict:
    if _current_project is None:
        return {"ok": False, "error": "no project loaded"}
    target = path or _current_path
    if not target:
        return {"ok": False, "error": "no destination path"}
    try:
        with _project_lock:
            _current_project.save(target)
            return {"ok": True, "saved_to": target}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def inspect(path: str) -> dict:
    """Read-only inspection of a project file."""
    if not HAS_PYFLP:
        return {"ok": False, "error": "pyflp not installed"}
    try:
        proj = flp.parse(path)
        channels = []
        for i, ch in enumerate(proj.channels or []):
            channels.append({
                "index": i,
                "name": getattr(ch, "name", f"ch{i}"),
                "volume": float(getattr(ch, "volume", 0.8) or 0.8),
                "pan": float(getattr(ch, "pan", 0.0) or 0.0),
                "mute": bool(getattr(ch, "mute", False)),
                "solo": bool(getattr(ch, "solo", False)),
            })
        return {
            "ok": True,
            "path": path,
            "name": getattr(proj, "title", None) or os.path.basename(path),
            "bpm": float(getattr(proj, "tempo", 120) or 120),
            "channels": channels,
            "channel_count": len(channels),
            "patterns": len(proj.patterns or []),
        }
    except Exception as exc:
        return {"ok": False, "error": f"inspect failed: {exc}"}
