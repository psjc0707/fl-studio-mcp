"""
MIDI Writer — generates standard MIDI files (SMF Format 0) for AI agents.

The .mid format is universally supported by FL Studio (File > Import MIDI).
This is more reliable than writing the proprietary .flp binary format.

MIDI structure:
  Header chunk:  'MThd' + 4-byte length + format + tracks + division
  Track chunk:   'MTrk' + 4-byte length + delta-time events + EOX (FF 2F 00)
"""

from __future__ import annotations
import struct
from pathlib import Path
from typing import Optional


class MIDIWriter:
    """Build a standard MIDI Format 0 file (single track)."""

    def __init__(self, ppq: int = 96, bpm: float = 120.0):
        self.ppq = ppq
        self.bpm = bpm
        self._events: list[tuple[int, bytes]] = []  # (tick, raw_midi_bytes)
        self._cursor = 0  # current position in ticks

    def set_tempo(self, bpm: float) -> None:
        """Insert a tempo change at the current cursor."""
        # MIDI tempo: microseconds per quarter note = 60_000_000 / bpm
        us_per_beat = int(60_000_000 / bpm)
        self._events.append((self._cursor, b"\xFF\x51\x03" + struct.pack(">I", us_per_per_quarter_note := us_per_beat)[1:]))

    def note_on(self, channel: int, pitch: int, velocity: int) -> None:
        self._events.append((self._cursor, bytes([0x90 | (channel & 0x0F), pitch & 0x7F, velocity & 0x7F])))

    def note_off(self, channel: int, pitch: int) -> None:
        self._events.append((self._cursor, bytes([0x80 | (channel & 0x0F), pitch & 0x7F, 0])))

    def note(self, channel: int, pitch: int, start_beat: float, length_beats: float, velocity: int = 100) -> None:
        """Schedule a note at start_beat with duration length_beats."""
        start_tick = int(start_beat * self.ppq)
        end_tick = int((start_beat + length_beats) * self.ppq)
        # Insert note_on at start_tick (sorting handles ordering)
        self._events.append((start_tick, bytes([0x90 | (channel & 0x0F), pitch & 0x7F, velocity & 0x7F])))
        self._events.append((end_tick, bytes([0x80 | (channel & 0x0F), pitch & 0x7F, 0])))

    def to_bytes(self) -> bytes:
        """Serialize to bytes."""
        # Sort events by tick
        sorted_events = sorted(self._events, key=lambda e: e[0])
        # Build track chunk
        track = bytearray()
        prev_tick = 0
        for tick, data in sorted_events:
            delta = tick - prev_tick
            track += self._varint(delta)
            track += data
            prev_tick = tick
        # End of track
        track += b"\xFF\x2F\x00"
        # Track header
        track_chunk = b"MTrk" + struct.pack(">I", len(track)) + bytes(track)

        # Header chunk
        header = b"MThd" + struct.pack(">I", 6) + struct.pack(">HHH", 0, 1, self.ppq)
        return header + track_chunk

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_bytes(self.to_bytes())
        return p

    @staticmethod
    def _varint(value: int) -> bytes:
        """Encode a variable-length quantity (MIDI standard)."""
        if value < 0:
            raise ValueError("varint must be non-negative")
        buffer = bytearray()
        buffer.append(value & 0x7F)
        value >>= 7
        while value:
            buffer.append((value & 0x7F) | 0x80)
            value >>= 7
        buffer.reverse()
        return bytes(buffer)


def notes_to_midi(channel: int, notes: list[int], start_beat: float,
                  length_beats: float, velocity: int = 100) -> list[tuple[int, int, int, float, float, int]]:
    """Convert a chord's notes to (channel, pitch, velocity, start, length, ...) tuples for MIDIWriter."""
    return [(channel, n, velocity, start_beat, length_beats) for n in notes]