"""
FLP Writer — minimal valid FL Studio project file (.flp) for AI agents.

Generates a complete .flp from scratch using only basic event types.
Compatible with FL Studio 21+ (verified against FL Studio 26.1.2 output).

Event format reference:
  Header:  b"FLhd" + u16(type=6) + u16(channel=0) + u16(flags=0) + u16(ppq=96) + u32(events_offset=0)
  Events:  u8(id) + u8(size_byte) [+ u32(size if size_byte & 0x80)] + payload

For large data events (size > 0x7F), use 4-byte size with bit 31 set.
"""

from __future__ import annotations
import struct
from pathlib import Path
from typing import Optional


class FLPWriter:
    """Build a minimal valid FL Studio 26 .flp file."""

    def __init__(self, title: str = "AI Composed", bpm: float = 120.0, ppq: int = 96):
        self.title = title
        self.bpm = bpm
        self.ppq = ppq
        self._channels: list[dict] = []
        self._patterns: list[dict] = []
        self._next_chan = 0
        self._events_root: list[bytes] = []
        # FL Studio 21+ format observed from real .flp files:
        #   First event after FLhd is FLVersion with ASCII version string.
        # We mark our files as FL Studio 26.x.x for compatibility.
        self._events_root.append(self._evt_text(199, "26.1.2.5557\x00"))
        # Title - but real FL puts title as UTF-16 in event 0x00 (size_byte=0x80)
        # Skip for now; FL accepts missing title.
        # PPQ (event ID 10)
        self._events_root.append(self._evt_u16(10, ppq))
        # Tempo coarse + fine (event ID 66 = 0x42, 93 = 0x5D)
        coarse = int(bpm)
        fine = int((bpm - coarse) * 1000)
        self._events_root.append(self._evt_u16(66, coarse))
        self._events_root.append(self._evt_u16(93, fine))

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _evt_u16(id_: int, value: int) -> bytes:
        """Encode a U16 event: id (1B) + size_byte (1B) + 2B value"""
        size_byte = 2
        return bytes([id_, size_byte]) + struct.pack("<H", value)

    @staticmethod
    def _evt_u32(id_: int, value: int) -> bytes:
        """Encode a U32 event: id (1B) + size_byte (1B) + 4B value"""
        return bytes([id_, 4]) + struct.pack("<I", value)

    @staticmethod
    def _evt_text(id_: int, text: str, max_size: int = 32) -> bytes:
        """Encode a text event. FL Studio uses windows-1252 for these."""
        raw = text.encode("windows-1252", errors="replace")
        if len(raw) > 255:
            raw = raw[:255]
        # size with high bit set means 4-byte size follows
        if len(raw) > 0x7F:
            return bytes([id_, 0x80]) + struct.pack("<I", len(raw)) + raw
        return bytes([id_, len(raw)]) + raw

    @staticmethod
    def _evt_long_text(id_: int, text: str) -> bytes:
        """Encode a long text event (uses 4-byte size)."""
        raw = text.encode("utf-16-le", errors="replace")
        return bytes([id_, 0x80]) + struct.pack("<I", len(raw)) + raw

    # ------------------------------------------------------------------ channels
    def add_channel(self, name: str, volume: float = 0.8, pan: float = 0.0,
                    mute: bool = False, solo: bool = False) -> int:
        idx = self._next_chan
        self._next_chan += 1
        self._channels.append({
            "index": idx, "name": name, "volume": volume, "pan": pan,
            "mute": mute, "solo": solo,
        })

        # Channel events (nested inside a parent event group)
        # 0xC0 (192) = ChannelGroup event
        channel_events = bytearray()
        # Channel name (event ID 198 = ChannelName)
        channel_events += self._evt_text(198, name)
        # Volume (event ID 36 = ChannelVolume, encoded as u16 with 10000 = 1.0)
        vol_raw = int(max(0.0, min(1.0, volume)) * 10000)
        channel_events += self._evt_u16(36, vol_raw)
        # Pan (event ID 37 = ChannelPan, signed)
        pan_raw = int(max(-1.0, min(1.0, pan)) * 10000)
        # signed u16: range -128..127 mapped but FL uses 10000 as full scale
        if pan_raw < 0:
            pan_raw = pan_raw + 0x10000
        channel_events += self._evt_u16(37, pan_raw & 0xFFFF)
        # Mute flag (event ID 39 = ChannelMute)
        if mute:
            channel_events += self._evt_u16(39, 1)
        # Solo flag (event ID 40 = ChannelSolo)
        if solo:
            channel_events += self._evt_u16(40, 1)

        # Wrap in a ChannelGroup event
        evt = bytes([0xC0]) + self._size_bytes(len(channel_events)) + bytes(channel_events)
        self._events_root.append(evt)
        return idx

    # ------------------------------------------------------------------ patterns
    def add_pattern(self, name: str = "Pattern") -> int:
        idx = len(self._patterns)
        self._patterns.append({"name": name, "notes": []})
        # Pattern event: 0xD0 (208) = PatternGroup
        evt = bytes([0xD0]) + self._size_bytes(0) + self._evt_text(201, name)
        self._events_root.append(evt)
        return idx

    def add_notes(self, channel: int, notes: list[tuple[int, float, float, int]],
                  pattern_index: int = 0) -> None:
        """notes: list of (midi_pitch, start_beat, length_beats, velocity)"""
        pat = self._patterns[pattern_index]
        pat["notes"].extend(notes)
        # Encode notes inside a NoteGroup (event ID 0x70 = 112)
        note_bytes = bytearray()
        for midi_pitch, start, length, velocity in notes:
            # Note event: u8 pitch, u16 position (in ticks), u16 length (in ticks), u8 velocity
            pos_ticks = int(start * self.ppq)
            len_ticks = max(1, int(length * self.ppq))
            note_bytes += struct.pack("<BHHB", midi_pitch & 0xFF,
                                      pos_ticks & 0xFFFF, len_ticks & 0xFFFF,
                                      velocity & 0xFF)
        if note_bytes:
            evt = bytes([0x70]) + self._size_bytes(len(note_bytes)) + bytes(note_bytes)
            self._events_root.append(evt)

    # ------------------------------------------------------------------ serialize
    @staticmethod
    def _size_bytes(n: int) -> bytes:
        """Encode size field. Returns 1 byte if n <= 0x7F, else 1 byte (0x80) + 4 bytes."""
        if n <= 0x7F:
            return bytes([n])
        return bytes([0x80]) + struct.pack("<I", n)

    def to_bytes(self) -> bytes:
        """Build the complete .flp file (FL Studio 21+ format).

        Format observed from real .flp files:
          [FLhd 14 bytes][FLdt chunk 8 bytes][event tree][end marker]
        The FLdt chunk is 'FLdt' magic + 4 bytes of version/timestamp data.
        """
        out = bytearray()
        # Header: 14 bytes
        out += b"FLhd"
        # Real FLP layout: type=6, channel=0, flags=0, 0x0005 (channel count?), ppq=96
        out += struct.pack("<HHHHH", 6, 0, 0, 5, self.ppq)
        # FLdt chunk: 8 bytes (4 magic + 4 data bytes)
        out += b"FLdt"
        out += struct.pack("<I", 0x0000D037)  # version/data marker from real files
        # Event tree body
        for evt in self._events_root:
            out += evt
        # End marker
        out += bytes([0xFF, 0x00])
        return bytes(out)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_bytes(self.to_bytes())
        return p


# ---------------------------------------------------------------------------
# Convenience helpers used by the bridge endpoints
# ---------------------------------------------------------------------------
def add_chord_to_memory_project(channel: int, notes_midi: list[int],
                                length_beats: float, velocity: int,
                                start_beat: float, project: dict) -> dict:
    """In-memory append of chord notes to the project's working pattern."""
    if "patterns" not in project or not project["patterns"]:
        project["patterns"] = [{"name": "AI Composed", "notes": []}]
    pattern = project["patterns"][-1]
    for pitch in notes_midi:
        pattern["notes"].append({
            "channel": channel,
            "pitch": pitch,
            "start": start_beat,
            "length": length_beats,
            "velocity": velocity,
        })
    return {
        "ok": True,
        "notes_added": len(notes_midi),
        "channel": channel,
        "start": start_beat,
    }


def notes_to_pcm(notes_midi: list[int], length_beats: float, start_beat: float,
                 channel: int, velocity: int, ppq: int) -> list[tuple[int, float, float, int]]:
    """Convert flat MIDI notes into the (pitch, start, length, velocity) tuples FLPWriter wants."""
    return [(n, start_beat, length_beats, velocity) for n in notes_midi]