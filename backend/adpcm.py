"""
adpcm.py — best-effort IMA/DVI ADPCM -> 16-bit PCM WAV for remote voice capture.

Android TV Voice (ATVV) v0.4 remotes stream 8 kHz IMA ADPCM in 134-byte frames:

    seq (u16 BE) | remote id (u8) | predictor (s16 BE) | step index (u8) | 128 bytes ADPCM

The frames arrive split across notifications (20 bytes each at MTU 23, or whole
at larger MTU), so we concatenate notification payloads and re-frame. If the
stream doesn't look like ATVV v0.4 (sequence numbers don't increment), we fall
back to decoding the raw byte stream as continuous IMA ADPCM.

The raw capture is always kept as well, so a wrong guess here never loses data.
"""
from __future__ import annotations

import io
import struct
import wave

STEP = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
]
INDEX = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]

ATVV_FRAME = 134
ATVV_HDR = 6


def decode_ima(data: bytes, predictor: int = 0, index: int = 0,
               high_first: bool = True) -> tuple[list[int], int, int]:
    out: list[int] = []
    index = max(0, min(88, index))
    for byte in data:
        for nib in ((byte >> 4, byte & 15) if high_first else (byte & 15, byte >> 4)):
            step = STEP[index]
            diff = step >> 3
            if nib & 1: diff += step >> 2
            if nib & 2: diff += step >> 1
            if nib & 4: diff += step
            predictor += -diff if nib & 8 else diff
            predictor = max(-32768, min(32767, predictor))
            index = max(0, min(88, index + INDEX[nib]))
            out.append(predictor)
    return out, predictor, index


def looks_like_atvv(stream: bytes) -> bool:
    n = len(stream) // ATVV_FRAME
    if n < 2:
        return False
    seqs = [struct.unpack_from(">H", stream, i * ATVV_FRAME)[0] for i in range(n)]
    good = sum(1 for a, b in zip(seqs, seqs[1:]) if (b - a) & 0xFFFF == 1)
    idx_ok = all(stream[i * ATVV_FRAME + 5] <= 88 for i in range(n))
    return idx_ok and good >= 0.8 * (n - 1)


def decode_atvv(stream: bytes) -> list[int]:
    pcm: list[int] = []
    for off in range(0, len(stream) - ATVV_FRAME + 1, ATVV_FRAME):
        pred = struct.unpack_from(">h", stream, off + 3)[0]
        idx = stream[off + 5]
        s, _, _ = decode_ima(stream[off + ATVV_HDR: off + ATVV_FRAME], pred, idx)
        pcm.extend(s)
    return pcm


def to_wav(pcm: list[int], rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(pcm), *pcm))
    return buf.getvalue()


def frames_to_wav(frames: list[bytes], codec: str = "auto", rate: int = 8000) -> tuple[bytes, dict]:
    """Notification payloads -> (wav bytes, info). codec: auto | atvv | ima | ima_lo."""
    stream = b"".join(frames)
    if codec == "auto":
        codec = "atvv" if looks_like_atvv(stream) else "ima"
    if codec == "atvv":
        pcm = decode_atvv(stream)
    else:
        pcm, _, _ = decode_ima(stream, high_first=(codec != "ima_lo"))
    peak = max((abs(x) for x in pcm), default=0)
    info = {"codec": codec, "bytes": len(stream), "frames": len(frames), "samples": len(pcm),
            "seconds": round(len(pcm) / rate, 2), "rate": rate, "peak": peak}
    return to_wav(pcm, rate), info


def energy(frame: bytes) -> float:
    """Cheap 0..1 level for the UI meter: mean ADPCM nibble magnitude."""
    if not frame:
        return 0.0
    tot = sum((b >> 4 & 7) + (b & 7) for b in frame)
    return min(1.0, tot / (len(frame) * 2 * 7.0))
