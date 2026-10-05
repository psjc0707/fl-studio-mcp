"""
Pure-Python FLP reader — minimal parser that extracts tempo, channel names,
and basic mixer state from an FL Studio project file.

Works around the pyflp 2.2.1 enum crash by reading the binary directly.
Based on the documented FLP event format.
"""

import struct
from pathlib import Path


# Known FLP event IDs (subset)
CHANNEL_NAME = 198       # ProjectID.ChannelName
CHANNEL_VOLUME = 36      # ProjectID.ChannelVolume
CHANNEL_PAN = 37         # ProjectID.ChannelPan
CHANNEL_MUTE = 39
CHANNEL_SOLO = 40
TEMPO_COARSE = 66
TEMPO_FINE = 93
PPQ = 10
TITLE = 195              # ProjectID.Title
FL_VERSION = 199


def read_windows1252(data: bytes) -> str:
    try:
        return data.decode("windows-1252", errors="replace")
    except Exception:
        return data.decode("latin-1", errors="replace")


def parse_flp(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"file not found: {p}"}

    data = p.read_bytes()
    if not data.startswith(b"FLhd"):
        return {"ok": False, "error": "not an FLP file (missing FLhd magic)"}

    info = {
        "ok": True,
        "path": str(p),
        "size_bytes": len(data),
        "title": None,
        "tempo_bpm": None,
        "ppq": None,
        "channels": [],
        "fl_version": None,
    }

    # Walk events (each is 2 bytes: [id] [size_byte] [data...])
    # Some events use 4 bytes for size when bit 7 of size_byte is set
    pos = 4  # skip FLhd
    while pos < len(data) - 4:
        if data[pos:pos+4] == b"FLst":
            pos += 4
            break
        # Header parsing: after FLhd comes type, channel, PPQ, ...
        if pos == 4:
            fl_type = struct.unpack_from("<H", data, pos)[0]
            info["fl_version"] = fl_type
            pos += 6  # 2 byte type, 4 bytes channel/misc
            continue
        pos += 1  # scan forward to find FLst

    # Event walk starting at FLst + 4 bytes
    while pos < len(data) - 2:
        evt_id = data[pos]
        pos += 1
        # Read size byte; high bit set means 4-byte size follows
        size_byte = data[pos]
        pos += 1
        if size_byte & 0x80:
            size = struct.unpack_from("<I", data, pos)[0] & 0x7FFFFFFF
            pos += 4
        else:
            size = size_byte

        if pos + size > len(data):
            break

        payload = data[pos:pos+size]
        pos += size

        if evt_id == TITLE:
            info["title"] = read_windows1252(payload).rstrip("\x00")
        elif evt_id == PPQ and size >= 2:
            info["ppq"] = struct.unpack_from("<H", payload, 0)[0]
        elif evt_id == FL_VERSION and size >= 4:
            ver = struct.unpack_from("<H", payload, 0)[0]
            info["fl_version"] = ver
        elif evt_id == TEMPO_COARSE and size >= 2:
            coarse = struct.unpack_from("<H", payload, 0)[0]
            info["tempo_bpm"] = float(coarse)
        elif evt_id == CHANNEL_NAME and size > 0:
            name = read_windows1252(payload).rstrip("\x00")
            info["channels"].append({
                "index": len(info["channels"]),
                "name": name,
                "volume": 0.8,
                "pan": 0.0,
                "mute": False,
                "solo": False,
            })
        elif evt_id == CHANNEL_VOLUME and size >= 4:
            # volume: u16 mapped to 0..1
            if info["channels"]:
                raw = struct.unpack_from("<H", payload, 0)[0]
                info["channels"][-1]["volume"] = round(raw / 10000.0, 3)
        elif evt_id == CHANNEL_PAN and size >= 4:
            if info["channels"]:
                raw = struct.unpack_from("<h", payload, 0)[0]
                info["channels"][-1]["pan"] = round(raw / 10000.0, 3)

    return info


if __name__ == "__main__":
    import json, sys
    path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\4l13n\Documents\Image-Line\FL Studio\Projects\Project_1\Project_1.flp"
    print(json.dumps(parse_flp(path), indent=2))
