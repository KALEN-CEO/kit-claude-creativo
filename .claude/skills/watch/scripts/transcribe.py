#!/usr/bin/env python3
"""Parse a WebVTT subtitle file into a clean, timestamped transcript.

YouTube auto-subs emit rolling-duplicate cues (each line appears 2-3 times as it
scrolls). We dedupe consecutive identical cues and merge their time ranges.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


# Adaptacion local: las horas son opcionales. El muxer WebVTT de
# ffmpeg escribe `MM:SS.mmm` cuando la hora es cero, y el regex upstream (solo
# `HH:MM:SS`) devolvia cero segmentos para cualquier subtitulo embebido.
TS_RE = re.compile(
    r"(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


def _to_seconds(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(path: str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    segments: list[dict] = []
    i = 0
    while i < len(lines):
        match = TS_RE.match(lines[i])
        if not match:
            i += 1
            continue

        start = _to_seconds(*match.groups()[:4])
        end = _to_seconds(*match.groups()[4:])
        i += 1

        cue_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            cleaned = TAG_RE.sub("", lines[i]).strip()
            if cleaned:
                cue_lines.append(cleaned)
            i += 1

        cue_text = " ".join(cue_lines).strip()
        if cue_text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": cue_text})
        i += 1

    return _dedupe(segments)


def _dedupe(segments: list[dict]) -> list[dict]:
    """Collapse rolling duplicates common in YouTube auto-subs."""
    out: list[dict] = []
    for seg in segments:
        if out and seg["text"] == out[-1]["text"]:
            out[-1]["end"] = seg["end"]
            continue
        if out and seg["text"].startswith(out[-1]["text"] + " "):
            out[-1]["text"] = seg["text"]
            out[-1]["end"] = seg["end"]
            continue
        out.append(seg)
    return out


def filter_range(
    segments: list[dict],
    start_seconds: float | None,
    end_seconds: float | None,
) -> list[dict]:
    """Return segments whose time range overlaps [start, end]."""
    if start_seconds is None and end_seconds is None:
        return segments
    lo = start_seconds if start_seconds is not None else float("-inf")
    hi = end_seconds if end_seconds is not None else float("inf")
    # Un segmento sin timestamp no se puede ubicar: se conserva y el informe lo
    # marca [--:--], en vez de descartarlo o inventarle una posicion.
    return [
        seg for seg in segments
        if seg.get("start") is None or seg.get("end") is None
        or (seg["end"] >= lo and seg["start"] <= hi)
    ]


NO_TIMESTAMP_STAMP = "[--:--]"


def format_transcript(segments: list[dict]) -> str:
    """Una linea por segmento. Un segmento sin timestamp (start None, caso de
    proveedores que devuelven solo texto) sale con `[--:--]`: no se inventa."""
    lines = []
    for seg in segments:
        start = seg.get("start")
        if start is None:
            stamp = NO_TIMESTAMP_STAMP
        else:
            start = int(start)
            stamp = f"[{start // 60:02d}:{start % 60:02d}]"
        lines.append(f"{stamp} {seg['text']}")
    return "\n".join(lines)


def covered_seconds(segments: list[dict]) -> float:
    """Segundos cubiertos por segmentos con timestamp (union de rangos)."""
    ranges = sorted(
        (float(s["start"]), float(s["end"]))
        for s in segments
        if s.get("start") is not None and s.get("end") is not None and s["end"] >= s["start"]
    )
    total = 0.0
    cur_start: float | None = None
    cur_end = 0.0
    for start, end in ranges:
        if cur_start is None or start > cur_end:
            if cur_start is not None:
                total += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_start is not None:
        total += cur_end - cur_start
    return round(total, 2)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: transcribe.py <vtt-path>", file=sys.stderr)
        raise SystemExit(2)
    print(format_transcript(parse_vtt(sys.argv[1])))
