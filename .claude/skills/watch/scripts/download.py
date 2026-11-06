#!/usr/bin/env python3
"""Descarga un video con yt-dlp o resuelve un archivo local, y localiza subtitulos.

Adaptacion local respecto del upstream bradautomates/claude-video 0.2.0:
- Idiomas de subtitulo configurables (default: espanol preferido, ingles
  alternativo), aplicados de forma coherente a la busqueda, la descarga y la
  seleccion del archivo.
- Procedencia declarada del subtitulo elegido: manual, automatico (ASR),
  automatico traducido, archivo adjunto local o stream embebido.
- Para archivos locales tambien se buscan subtitulos adjuntos asociados de
  forma exacta al nombre completo del video (`clip.srt`, `clip.vtt`,
  `clip.es.srt`, `clip.es-419.vtt`; nunca `clip10.es.srt` para `clip`) y
  streams de subtitulo embebidos en el contenedor. Nada se escribe junto al
  original: lo extraido va al directorio de trabajo.
- yt-dlp corre aislado: `--ignore-config --no-plugin-dirs`, sin cookies,
  login ni netrc.
- Sin sugerencias de `brew install`.

Titulo, uploader y subtitulos son contenido no confiable: se transportan como
datos, nunca se interpretan como instrucciones.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from config import DEFAULT_SUB_LANGS, lang_matches, normalize_sub_langs  # noqa: E402


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}
SUBTITLE_EXTS = (".vtt", ".srt")

# Aislamiento de yt-dlp: no heredar configuraciones externas ni plugins.
# `--ignore-config` descarta todo archivo de configuracion; `--no-plugin-dirs`
# vacia los directorios de plugins (ambos verificados en yt-dlp 2026.08.19).
# Sin cookies, login, netrc ni rutas de configuracion: solo las opciones
# explicitas de cada comando.
YTDLP_ISOLATION = ("--ignore-config", "--no-plugin-dirs")

ORIGIN_MANUAL = "manual (subtitulo subido por el canal)"
ORIGIN_AUTO = "automatica (ASR de la plataforma; no es transcripcion humana)"
ORIGIN_AUTO_TRANSLATED = (
    "automatica traducida (ASR + traduccion de la plataforma; NO representa el audio original)"
)
ORIGIN_SIDECAR = "archivo de subtitulo adjunto al video local"
ORIGIN_EMBEDDED = "stream de subtitulo embebido en el contenedor del video"
ORIGIN_UNKNOWN = "desconocida"


def is_url(source: str) -> bool:
    if source.startswith("-"):
        return False
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _require(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(
            f"{name} no esta instalado o no esta en PATH. "
            f"Correr: python \"{SCRIPT_DIR / 'setup.py'}\" --check"
        )


def _empty_subtitle_fields(sub_langs: list[str]) -> dict:
    return {
        "subtitle_path": None,
        "subtitle_lang": None,
        "subtitle_origin": None,
        "subtitle_requested_langs": list(sub_langs),
        "subtitle_lang_match": False,
    }


# --------------------------------------------------------------------------- #
# Seleccion de archivo de subtitulo por prioridad de idioma
# --------------------------------------------------------------------------- #

# Etiqueta de idioma admitida entre el nombre del video y la extension:
# `es`, `spa`, `es-419`, `en-US`, `en-orig`. Nada mas.
LANG_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def classify_subtitle_name(name: str, stem: str) -> tuple[bool, str | None]:
    """Decide si `name` es un subtitulo asociado inequivocamente al video `stem`.

    Acepta solo `stem.srt`, `stem.vtt`, `stem.<lang>.srt` y `stem.<lang>.vtt`,
    comparando el nombre completo de forma literal (sin glob, sin prefijos).
    `clip10.es.srt` no es candidato de `clip1`. Devuelve (es_candidato, idioma)."""
    prefix = stem + "."
    if not name.startswith(prefix):
        return False, None
    rest = name[len(prefix):]
    if rest.lower() in tuple(ext.lstrip(".") for ext in SUBTITLE_EXTS):
        return True, None
    for ext in SUBTITLE_EXTS:
        if rest.lower().endswith(ext):
            lang = rest[: -len(ext)]
            if LANG_TAG_RE.match(lang):
                return True, lang
            return False, None
    return False, None


def subtitle_lang_from_name(path: Path, stem: str) -> str | None:
    """`video.es.vtt` -> `es`; `video.en-US.vtt` -> `en-US`; `video.vtt` -> None."""
    ok, lang = classify_subtitle_name(path.name, stem)
    return lang if ok else None


def exact_subtitle_candidates(directory: Path, stem: str) -> list[Path]:
    """Archivos de subtitulo del directorio asociados exactamente a `stem`.
    Recorre entradas reales: los caracteres del nombre se tratan literalmente."""
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return []
    return [
        p for p in entries
        if p.is_file() and classify_subtitle_name(p.name, stem)[0]
    ]


def pick_subtitle(
    candidates: list[Path], sub_langs: list[str], stem: str
) -> tuple[Path | None, str | None, bool]:
    """Elige el subtitulo respetando el orden de `sub_langs`.

    Devuelve (ruta, codigo_de_idioma, coincide_con_pedido). Si ningun candidato
    coincide con los idiomas pedidos, devuelve el primero disponible con
    `coincide=False` para que el informe lo declare."""
    if not candidates:
        return None, None, False
    for lang in sub_langs:
        for cand in candidates:
            code = subtitle_lang_from_name(cand, stem)
            if code and lang_matches(lang, code):
                return cand, code, True
    first = candidates[0]
    return first, subtitle_lang_from_name(first, stem), False


def _subtitle_candidates(out_dir: Path, stem: str) -> list[Path]:
    return exact_subtitle_candidates(out_dir, stem)


# --------------------------------------------------------------------------- #
# info.json de yt-dlp: metadata y procedencia
# --------------------------------------------------------------------------- #

def _read_info_raw(info_path: Path) -> dict:
    if not info_path.exists():
        return {}
    try:
        raw = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[watch] no se pudo leer info.json: {exc}", file=sys.stderr)
        return {}
    return raw if isinstance(raw, dict) else {}


def _summarize_info(raw: dict, url: str) -> dict:
    if not raw:
        return {"url": url}
    return {
        "title": raw.get("title"),
        "uploader": raw.get("uploader") or raw.get("channel"),
        "duration": raw.get("duration"),
        "url": raw.get("webpage_url") or url,
    }


def subtitle_origin_from_info(raw: dict, code: str | None) -> str:
    """Procedencia del subtitulo segun lo que yt-dlp declara en info.json.

    Heuristica: si el idioma figura en `subtitles` es manual; si figura en
    `automatic_captions` es ASR, y si el nombre de la pista sugiere traduccion
    ("from", "translat", "traduc") se marca como traducida."""
    if not code:
        return ORIGIN_UNKNOWN
    manual = raw.get("subtitles") or {}
    auto = raw.get("automatic_captions") or {}
    if code in manual:
        return ORIGIN_MANUAL
    if code in auto:
        tracks = auto.get(code) or []
        names = " ".join(
            str(t.get("name", "")) for t in tracks if isinstance(t, dict)
        ).lower()
        if any(marker in names for marker in ("from", "translat", "traduc")):
            return ORIGIN_AUTO_TRANSLATED
        return ORIGIN_AUTO
    return ORIGIN_UNKNOWN


def sub_langs_arg(sub_langs: list[str]) -> str:
    """yt-dlp trata cada item como regex anclada al inicio y le agrega `$`.
    `es(-.*)?` toma `es`, `es-419`, `es-ES` y no toma `est` (estonio)."""
    return ",".join(f"{lang}(-.*)?" for lang in sub_langs)


def _pick_video(out_dir: Path) -> Path | None:
    for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus"):
        for candidate in out_dir.glob(f"video*{ext}"):
            return candidate
    for candidate in out_dir.glob("video.*"):
        if candidate.suffix.lower() in VIDEO_EXTS:
            return candidate
    return None


def _finish_remote(
    out_dir: Path, url: str, sub_langs: list[str], video: Path | None, downloaded: bool
) -> dict:
    raw = _read_info_raw(out_dir / "video.info.json")
    subtitle, code, matched = pick_subtitle(
        _subtitle_candidates(out_dir, "video"), sub_langs, "video"
    )
    result = {
        "video_path": str(video) if video else None,
        "info": _summarize_info(raw, url),
        "downloaded": downloaded,
        **_empty_subtitle_fields(sub_langs),
    }
    if subtitle:
        result.update({
            "subtitle_path": str(subtitle),
            "subtitle_lang": code,
            "subtitle_origin": subtitle_origin_from_info(raw, code),
            "subtitle_lang_match": matched,
        })
    return result


def fetch_captions(url: str, out_dir: Path, sub_langs: list[str] | None = None) -> dict:
    """Metadata y subtitulos disponibles, sin descargar el video."""
    _require("yt-dlp")
    sub_langs = normalize_sub_langs(sub_langs)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        *YTDLP_ISOLATION,
        "--skip-download",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", sub_langs_arg(sub_langs),
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
        "-o", str(out_dir / "video.%(ext)s"),
        "--",
        url,
    ]
    subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    return _finish_remote(out_dir, url, sub_langs, video=None, downloaded=False)


def download_url(
    url: str,
    out_dir: Path,
    audio_only: bool = False,
    sub_langs: list[str] | None = None,
) -> dict:
    _require("yt-dlp")
    sub_langs = normalize_sub_langs(sub_langs)
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = "ba/bestaudio" if audio_only else "bv*[height<=720]+ba/b[height<=720]/bv+ba/b"
    cmd = [
        "yt-dlp",
        *YTDLP_ISOLATION,
        "-N", "8",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", sub_langs_arg(sub_langs),
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
        "-o", str(out_dir / "video.%(ext)s"),
        "--",
        url,
    ]
    # yt-dlp puede salir distinto de cero si fallo una variante de subtitulo
    # aunque el video haya bajado bien. "Hay archivo de video" es el exito.
    result = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    video = _pick_video(out_dir)
    if video is None:
        raise SystemExit(
            f"yt-dlp no produjo un archivo de video en {out_dir} (exit {result.returncode})"
        )
    return _finish_remote(out_dir, url, sub_langs, video=video, downloaded=True)


# --------------------------------------------------------------------------- #
# Archivos locales: subtitulos adjuntos y embebidos
# --------------------------------------------------------------------------- #

def _sidecar_candidates(video: Path) -> list[Path]:
    return exact_subtitle_candidates(video.parent, video.stem)


def probe_subtitle_streams(video: Path) -> list[dict]:
    """Streams de subtitulo del contenedor, con indice relativo y tag de idioma."""
    if shutil.which("ffprobe") is None:
        return []
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-select_streams", "s", str(video),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    try:
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for rel_index, stream in enumerate(streams):
        tags = stream.get("tags") or {}
        out.append({
            "rel_index": rel_index,
            "codec": stream.get("codec_name"),
            "language": tags.get("language"),
            "title": tags.get("title"),
        })
    return out


def pick_embedded_stream(streams: list[dict], sub_langs: list[str]) -> tuple[dict | None, bool]:
    for lang in sub_langs:
        for stream in streams:
            if lang_matches(lang, stream.get("language")):
                return stream, True
    if len(streams) == 1:
        return streams[0], False
    return None, False


def extract_embedded_subtitle(video: Path, rel_index: int, out_path: Path) -> bool:
    """Convierte un stream de subtitulo de texto a WebVTT en `out_path`.
    Subtitulos bitmap (PGS, DVD) no convierten: devuelve False sin romper."""
    if shutil.which("ffmpeg") is None:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video), "-map", f"0:s:{rel_index}", "-f", "webvtt", str(out_path),
        ],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def resolve_local(
    path: str, out_dir: Path | None = None, sub_langs: list[str] | None = None
) -> dict:
    """Resuelve un archivo local (solo lectura) y busca sus subtitulos."""
    sub_langs = normalize_sub_langs(sub_langs)
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"Archivo no encontrado: {p}")
    if not p.is_file():
        raise SystemExit(f"La ruta no es un archivo: {p}")
    if p.suffix.lower() not in VIDEO_EXTS:
        print(
            f"[watch] aviso: {p.suffix} no es una extension de video conocida; se intenta igual",
            file=sys.stderr,
        )
    result = {
        "video_path": str(p),
        "info": {"title": p.name, "url": str(p)},
        "downloaded": False,
        **_empty_subtitle_fields(sub_langs),
    }

    sidecar, code, matched = pick_subtitle(_sidecar_candidates(p), sub_langs, p.stem)
    if sidecar:
        result.update({
            "subtitle_path": str(sidecar),
            "subtitle_lang": code,
            "subtitle_origin": ORIGIN_SIDECAR,
            "subtitle_lang_match": matched,
        })
        return result

    streams = probe_subtitle_streams(p)
    if streams and out_dir is not None:
        stream, matched = pick_embedded_stream(streams, sub_langs)
        if stream is not None:
            lang = stream.get("language") or "und"
            target = out_dir / "subs" / f"embedded.{lang}.vtt"
            if extract_embedded_subtitle(p, int(stream["rel_index"]), target):
                result.update({
                    "subtitle_path": str(target),
                    "subtitle_lang": stream.get("language"),
                    "subtitle_origin": ORIGIN_EMBEDDED,
                    "subtitle_lang_match": matched,
                })
            else:
                print(
                    f"[watch] el stream de subtitulo embebido ({stream.get('codec')}) "
                    "no se pudo convertir a texto",
                    file=sys.stderr,
                )
    return result


def download(
    source: str,
    out_dir: Path,
    audio_only: bool = False,
    sub_langs: list[str] | None = None,
) -> dict:
    if is_url(source):
        return download_url(source, out_dir, audio_only=audio_only, sub_langs=sub_langs)
    return resolve_local(source, out_dir=out_dir, sub_langs=sub_langs)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("uso: download.py <url-o-ruta> <dir-salida> [idiomas ej. es,en]", file=sys.stderr)
        raise SystemExit(2)
    langs = normalize_sub_langs(sys.argv[3]) if len(sys.argv) > 3 else list(DEFAULT_SUB_LANGS)
    print(json.dumps(
        download(sys.argv[1], Path(sys.argv[2]), sub_langs=langs), indent=2, ensure_ascii=False
    ))
