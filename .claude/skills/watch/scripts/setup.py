#!/usr/bin/env python3
"""Preflight de dependencias de /watch.

Modos:
  setup.py --check   Silencioso. Exit 0 si ffmpeg, ffprobe y yt-dlp resuelven;
                     exit 2 con una linea en stderr si falta alguno.
  setup.py --json    Estado estructurado: binarios, rutas, versiones y
                     preferencias no secretas.
  setup.py           Igual que --json pero legible. NO instala nada.

Adaptacion local respecto del upstream:
- Sin instalador: no ejecuta brew, winget ni pip. Solo informa que falta y el
  comando oficial para instalarlo.
- Sin .env: no lee, crea ni modifica archivos .env, no consulta credenciales,
  no imprime variables de entorno.
- Sin "primer uso" ni clave de Whisper: la transcripcion externa esta
  desactivada en esta instalacion y el preflight no la contempla.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from config import REQUIRED_BINARIES, bootstrap_path, get_config  # noqa: E402


INSTALL_HINTS = {
    "Windows": {
        "ffmpeg": "winget install --id Gyan.FFmpeg --exact",
        "ffprobe": "winget install --id Gyan.FFmpeg --exact   (ffprobe viene con ffmpeg)",
        "yt-dlp": "winget install --id yt-dlp.yt-dlp --exact",
    },
    "Darwin": {
        "ffmpeg": "brew install ffmpeg",
        "ffprobe": "brew install ffmpeg",
        "yt-dlp": "brew install yt-dlp",
    },
    "Linux": {
        "ffmpeg": "sudo apt install ffmpeg   (o el gestor de la distro)",
        "ffprobe": "sudo apt install ffmpeg",
        "yt-dlp": "pipx install yt-dlp",
    },
}


def _version(name: str, path: str | None) -> str | None:
    if not path:
        return None
    argv = [path, "--version"] if name == "yt-dlp" else [path, "-version"]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (result.stdout or result.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else None


def status() -> dict:
    resolved = bootstrap_path()
    missing = [b for b in REQUIRED_BINARIES if not resolved.get(b)]
    cfg = get_config()
    return {
        "status": "ready" if not missing else "needs_install",
        "can_proceed": not missing,
        "missing_binaries": missing,
        "binaries": resolved,
        "versions": {b: _version(b, resolved.get(b)) for b in REQUIRED_BINARIES},
        "external_transcription": "disabled",
        "watch_detail": cfg["detail"],
        "sub_langs": cfg["sub_langs"],
        "resolution": cfg["resolution"],
        "config_file": cfg["config_file"],
        "platform": platform.system(),
    }


def _hints(missing: list[str]) -> list[str]:
    table = INSTALL_HINTS.get(platform.system(), {})
    seen: list[str] = []
    for name in missing:
        hint = table.get(name, f"instalar {name} desde su fuente oficial")
        if hint not in seen:
            seen.append(hint)
    return seen


def cmd_check() -> int:
    s = status()
    if s["can_proceed"]:
        return 0
    sys.stderr.write(
        "[watch] faltan binarios: " + ", ".join(s["missing_binaries"]) + ". Instalar con: "
        + " | ".join(_hints(s["missing_binaries"])) + "\n"
    )
    sys.stderr.flush()
    return 2


def cmd_json() -> int:
    json.dump(status(), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_report() -> int:
    s = status()
    print("[watch] preflight de dependencias")
    for name in REQUIRED_BINARIES:
        path = s["binaries"].get(name)
        version = s["versions"].get(name) or "version no leida"
        print(f"  {name:8} {'OK ' if path else 'FALTA'}  {path or ''}  {version if path else ''}")
    print(f"  detalle por defecto: {s['watch_detail']}  |  idiomas de subtitulo: {', '.join(s['sub_langs'])}")
    print("  transcripcion externa de audio: desactivada en esta instalacion")
    if s["missing_binaries"]:
        print("  instalar:")
        for hint in _hints(s["missing_binaries"]):
            print(f"    {hint}")
        return 2
    return 0


def main() -> int:
    if len(sys.argv) > 1:
        if sys.argv[1] == "--check":
            return cmd_check()
        if sys.argv[1] == "--json":
            return cmd_json()
    return cmd_report()


if __name__ == "__main__":
    raise SystemExit(main())
