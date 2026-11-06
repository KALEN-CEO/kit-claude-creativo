#!/usr/bin/env python3
"""Configuracion compartida de /watch.

Adaptacion local respecto del upstream bradautomates/claude-video 0.2.0:
- No lee, crea ni modifica archivos .env. No consulta variables de entorno
  para preferencias ni para credenciales. No busca claves de ningun proveedor.
- Las preferencias no secretas viven en `watch-config.json`, junto a SKILL.md,
  y cualquier argumento de linea de comando las sobreescribe.
- `bootstrap_path()` resuelve ffmpeg, ffprobe y yt-dlp. Si la shell todavia no
  refresco su PATH (caso tipico: VS Code en Windows tras instalar con winget),
  agrega al PATH de ESTE proceso las carpetas de winget del usuario. No toca el
  sistema ni persiste nada.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
CONFIG_FILE = SKILL_DIR / "watch-config.json"

DEFAULT_DETAIL = "balanced"
DETAILS = {"transcript", "efficient", "balanced", "token-burner"}
DEFAULT_SUB_LANGS = ["es", "en"]
DEFAULT_RESOLUTION = 512

REQUIRED_BINARIES = ["ffmpeg", "ffprobe", "yt-dlp"]

# ISO 639-1 (lo que se pide por argumento) -> alias que aparecen en tags de
# streams (ISO 639-2 en mp4/mkv) y en sufijos de archivo de yt-dlp.
LANG_ALIASES: dict[str, set[str]] = {
    "es": {"es", "spa"},
    "en": {"en", "eng"},
    "pt": {"pt", "por"},
    "fr": {"fr", "fra", "fre"},
    "it": {"it", "ita"},
    "de": {"de", "deu", "ger"},
}

_ALLOWED_CONFIG_KEYS = {"detail", "sub_langs", "resolution", "transcription"}

# Preferencias NO secretas de transcripcion. `provider: null` = desactivada salvo
# `--transcribe` explicito. `env_file` es la RUTA de un archivo de entorno que
# solo se abre cuando la transcripcion esta activa (nunca en el camino por defecto).
DEFAULT_TRANSCRIPTION = {
    "provider": None,
    "mode": "auto",            # auto: solo si faltan subtitulos o cubren poco; force: siempre
    "min_subtitle_coverage": 0.5,
    "env_file": None,
    "language": None,          # codigo ISO 639-1 sugerido al proveedor; None = deteccion automatica
    "models": {},              # override por proveedor, ej. {"groq": "whisper-large-v3"}
}
_TRANSCRIPTION_KEYS = set(DEFAULT_TRANSCRIPTION)


def normalize_transcription(value) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_TRANSCRIPTION.items()}
    if not isinstance(value, dict):
        return out
    for key in _TRANSCRIPTION_KEYS:
        if key in value:
            out[key] = value[key]
    if out["mode"] not in ("auto", "force"):
        out["mode"] = "auto"
    try:
        out["min_subtitle_coverage"] = min(1.0, max(0.0, float(out["min_subtitle_coverage"])))
    except (TypeError, ValueError):
        out["min_subtitle_coverage"] = DEFAULT_TRANSCRIPTION["min_subtitle_coverage"]
    if not isinstance(out["models"], dict):
        out["models"] = {}
    return out


def read_config_file(path: Path | None = None) -> dict:
    """Lee `watch-config.json`. Sin archivo, JSON invalido o no-objeto: {}.
    Solo conserva claves conocidas y no secretas."""
    if path is None:
        path = CONFIG_FILE
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in _ALLOWED_CONFIG_KEYS}


def normalize_sub_langs(value) -> list[str]:
    """Acepta lista o string separado por comas. Devuelve codigos en minuscula,
    sin duplicados, en orden de preferencia. Vacio -> DEFAULT_SUB_LANGS."""
    if value is None:
        return list(DEFAULT_SUB_LANGS)
    items = value.split(",") if isinstance(value, str) else list(value)
    out: list[str] = []
    for item in items:
        code = str(item).strip().lower()
        if code and code not in out:
            out.append(code)
    return out or list(DEFAULT_SUB_LANGS)


def get_config() -> dict[str, object]:
    file_values = read_config_file()
    detail = file_values.get("detail") or DEFAULT_DETAIL
    if detail not in DETAILS:
        detail = DEFAULT_DETAIL
    resolution = file_values.get("resolution") or DEFAULT_RESOLUTION
    try:
        resolution = int(resolution)
    except (TypeError, ValueError):
        resolution = DEFAULT_RESOLUTION
    if resolution < 64:
        resolution = DEFAULT_RESOLUTION
    return {
        "detail": detail,
        "sub_langs": normalize_sub_langs(file_values.get("sub_langs")),
        "resolution": resolution,
        "transcription": normalize_transcription(file_values.get("transcription")),
        "config_file": str(CONFIG_FILE),
    }


def frame_cap(detail: str) -> int | None:
    if detail == "efficient":
        return 50
    if detail == "balanced":
        return 100
    if detail == "token-burner":
        return None
    if detail == "transcript":
        return None
    return 100


def lang_matches(requested: str, observed: str | None) -> bool:
    """True si `observed` (tag de stream o sufijo de archivo de subtitulo)
    corresponde al idioma `requested`. Tolera region (`es-419`, `en-US`) y las
    variantes `-orig` de yt-dlp."""
    if not observed:
        return False
    req = requested.strip().lower()
    obs = observed.strip().lower()
    base = obs.split("-")[0].split("_")[0]
    aliases = LANG_ALIASES.get(req, {req})
    return obs in aliases or base in aliases


def _winget_candidates(name: str) -> list[Path]:
    """Carpetas donde winget deja `name.exe` para el usuario actual."""
    base = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet"
    exe = f"{name}.exe"
    found: list[Path] = []
    links = base / "Links" / exe
    if links.exists():
        found.append(links.parent)
    packages = base / "Packages"
    if packages.is_dir():
        for pattern in (f"*/{exe}", f"*/*/{exe}", f"*/*/bin/{exe}"):
            for hit in sorted(packages.glob(pattern)):
                if hit.parent not in found:
                    found.append(hit.parent)
    return found


def bootstrap_path() -> dict[str, str | None]:
    """Resuelve los binarios requeridos. Si alguno no esta en PATH, busca las
    instalaciones winget del usuario y agrega su carpeta al PATH de este
    proceso. Devuelve {nombre: ruta_resuelta | None}."""
    resolved: dict[str, str | None] = {}
    for name in REQUIRED_BINARIES:
        path = shutil.which(name)
        if path is None:
            for candidate_dir in _winget_candidates(name):
                os.environ["PATH"] = str(candidate_dir) + os.pathsep + os.environ.get("PATH", "")
                path = shutil.which(name)
                if path:
                    break
        resolved[name] = path
    return resolved


bootstrap_path()
