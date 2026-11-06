#!/usr/bin/env python3
"""Transcripcion automatica de audio via API compatible con Whisper (Groq u OpenAI).

ESTADO EN ESTA INSTALACION: MODULO ACTIVABLE, NUNCA POR DEFECTO.
- `watch.py` lo importa SOLO cuando se pasa `--transcribe <proveedor>`. Sin
  ese flag no se importa, no se extrae audio y no se abre ninguna conexion.
- El proveedor es explicito: `groq` u `openai`. No hay fallback silencioso de
  uno a otro. Si el elegido falla, el informe lo declara y no prueba con el otro.
- La credencial llega resuelta por `credentials.py` (variable de entorno o
  archivo de entorno indicado). Este modulo no lee `.env`, no consulta
  variables de entorno y nunca imprime la clave.
- Timestamps: se conservan los que devuelve el proveedor (`verbose_json`,
  segmentos con `start`/`end`). Si el proveedor solo devuelve texto, los
  segmentos salen con `start=None, end=None` y el informe lo marca `[--:--]`:
  NO se inventan tiempos.
- Cobertura: cada tramo (chunk) que falle queda registrado con su rango en
  segundos y se reporta como "tramo no transcrito". Si fallan todos, falla la
  transcripcion.

Endpoints y modelos verificados en la documentacion oficial el 2026-09-14:
- Groq:   POST https://api.groq.com/openai/v1/audio/transcriptions
          modelos `whisper-large-v3-turbo`, `whisper-large-v3`; `verbose_json`
          devuelve segmentos con start/end. Tope 25 MB por archivo (free tier).
- OpenAI: POST https://api.openai.com/v1/audio/transcriptions
          `whisper-1` es el unico modelo que devuelve `verbose_json` con
          segmentos y timestamps; `gpt-4o-transcribe` y variantes devuelven
          texto sin tiempos. Tope 25 MB por archivo.

Origen: bradautomates/claude-video 0.2.0 (MIT). Stdlib puro.
"""
from __future__ import annotations

import io
import json
import math
import mimetypes
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


PROVIDERS: dict[str, dict] = {
    "groq": {
        "endpoint": "https://api.groq.com/openai/v1/audio/transcriptions",
        "default_model": "whisper-large-v3-turbo",
        "models_with_timestamps": {"whisper-large-v3-turbo", "whisper-large-v3"},
        "credential_names": ("GROQ_API_KEY",),
    },
    "openai": {
        "endpoint": "https://api.openai.com/v1/audio/transcriptions",
        "default_model": "whisper-1",
        "models_with_timestamps": {"whisper-1"},
        "credential_names": ("OPENAI_API_KEY",),
    },
}

# Groq (free tier) y OpenAI limitan la subida a 25 MB. Margen para el framing multipart.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024

MAX_ATTEMPTS = 4       # intento inicial + 3 reintentos
MAX_429_RETRIES = 2
RETRY_BASE_DELAY = 2.0


def provider_spec(provider: str) -> dict:
    spec = PROVIDERS.get((provider or "").lower())
    if not spec:
        raise SystemExit(
            f"Proveedor de transcripcion desconocido: {provider!r}. Validos: {', '.join(sorted(PROVIDERS))}."
        )
    return spec


def plan_chunks(
    total_seconds: float,
    total_bytes: int,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> list[tuple[float, float]]:
    """Divide una duracion en tramos contiguos (offset, duracion) bajo max_bytes.
    Devuelve un solo tramo si el audio ya entra."""
    if total_bytes <= max_bytes or total_seconds <= 0:
        return [(0.0, total_seconds)]
    n = math.ceil(total_bytes / max_bytes)
    chunk = total_seconds / n
    plan: list[tuple[float, float]] = []
    for i in range(n):
        offset = i * chunk
        duration = (total_seconds - offset) if i == n - 1 else chunk
        plan.append((round(offset, 3), round(duration, 3)))
    return plan


def extract_audio(video_path: str, out_path: Path) -> Path:
    """Extrae audio mono 16 kHz 64 kbps mp3 (~480 kB/min)."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg no esta instalado o no esta en PATH. Correr: python setup.py --check")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(Path(video_path).resolve()),
        "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg no pudo extraer el audio: {result.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SystemExit("ffmpeg no produjo audio: el video puede no tener pista de audio")
    return out_path


def audio_duration(audio_path: Path) -> float:
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe no esta instalado o no esta en PATH. Correr: python setup.py --check")
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(audio_path.resolve())],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe fallo: {result.stderr.strip()}")
    fmt = json.loads(result.stdout or "{}").get("format", {})
    return float(fmt.get("duration") or 0.0)


def split_audio(full_audio: Path, work_dir: Path, plan: list[tuple[float, float]]) -> list[tuple[Path, float, float]]:
    """Corta el audio en tramos segun el plan. Devuelve (ruta, offset, duracion)."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg no esta instalado o no esta en PATH. Correr: python setup.py --check")
    work_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[tuple[Path, float, float]] = []
    for index, (offset, duration) in enumerate(plan):
        out_path = work_dir / f"chunk_{index:03d}.mp3"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{offset:.3f}", "-i", str(full_audio.resolve()), "-t", f"{duration:.3f}",
            "-c", "copy", str(out_path.resolve()),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            raise SystemExit(f"ffmpeg no pudo cortar el tramo {index + 1}: {result.stderr.strip()}")
        chunks.append((out_path, offset, duration))
    return chunks


def _build_multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = f"----WatchBoundary{uuid.uuid4().hex}"
    eol = b"\r\n"
    buf = io.BytesIO()
    for name, value in fields.items():
        buf.write(f"--{boundary}".encode()); buf.write(eol)
        buf.write(f'Content-Disposition: form-data; name="{name}"'.encode()); buf.write(eol)
        buf.write(eol)
        buf.write(str(value).encode()); buf.write(eol)
    mimetype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    buf.write(f"--{boundary}".encode()); buf.write(eol)
    buf.write(f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode())
    buf.write(eol)
    buf.write(f"Content-Type: {mimetype}".encode()); buf.write(eol)
    buf.write(eol)
    buf.write(file_path.read_bytes())
    buf.write(eol)
    buf.write(f"--{boundary}--".encode()); buf.write(eol)
    return buf.getvalue(), boundary


REDACTED = "[credencial oculta]"


def _redact(text, api_key: str | None) -> str:
    """Oculta la credencial en cualquier diagnostico antes de mostrarlo o
    guardarlo. Una respuesta del proveedor NO es segura por venir del proveedor:
    un 401 puede repetir en su cuerpo la clave enviada."""
    out = str(text)
    if api_key and len(api_key) >= 4:
        out = out.replace(api_key, REDACTED)
    return out


def _read_error_body(exc: urllib.error.HTTPError, api_key: str | None = None) -> str:
    try:
        body = exc.read()
    except Exception:
        return ""
    if not body:
        return ""
    try:
        return f" ({_redact(body.decode('utf-8', errors='replace'), api_key)[:300]})"
    except Exception:
        return ""


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    header = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _post_transcription(
    endpoint: str, api_key: str, model: str, audio_path: Path, language: str | None, timestamps: bool,
) -> dict:
    fields = {"model": model, "temperature": "0"}
    fields["response_format"] = "verbose_json" if timestamps else "json"
    if language:
        fields["language"] = language
    body, boundary = _build_multipart(fields, audio_path)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        # Groq esta detras de Cloudflare: el UA por defecto de urllib dispara un 403.
        "User-Agent": "watch-skill/1.0 (python-urllib)",
    }
    context = ssl.create_default_context()
    rate_limit_hits = 0
    last_exc: Exception | None = None
    last_detail = ""
    for attempt in range(MAX_ATTEMPTS):
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300, context=context) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc, api_key)
            last_exc, last_detail = exc, detail
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SystemExit(f"la API de transcripcion rechazo el pedido: HTTP {exc.code}{detail}")
            if exc.code == 429:
                rate_limit_hits += 1
                if rate_limit_hits >= MAX_429_RETRIES:
                    raise SystemExit(f"la API de transcripcion limito el ritmo: HTTP 429{detail}")
                delay = _retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt) + 1
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
            if attempt < MAX_ATTEMPTS - 1:
                print(f"[watch] transcripcion HTTP {exc.code}, reintento en {delay:.1f}s "
                      f"({attempt + 2}/{MAX_ATTEMPTS})", file=sys.stderr)
                time.sleep(delay)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last_exc, last_detail = exc, ""
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                print(f"[watch] transcripcion: error de red ({type(exc).__name__}), reintento en "
                      f"{delay:.1f}s ({attempt + 2}/{MAX_ATTEMPTS})", file=sys.stderr)
                time.sleep(delay)
            continue
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"la API de transcripcion devolvio algo que no es JSON: {exc}")
    raise SystemExit(_redact(f"la transcripcion fallo tras {MAX_ATTEMPTS} intentos: {last_exc}{last_detail}", api_key))


def shift_segments(segments: list[dict], offset_seconds: float) -> list[dict]:
    """Desplaza start/end por el offset del tramo. Los segmentos sin timestamp
    (None) se dejan sin timestamp: no se les inventa uno."""
    if offset_seconds == 0:
        return segments
    out = []
    for seg in segments:
        if seg.get("start") is None or seg.get("end") is None:
            out.append(dict(seg))
            continue
        out.append({
            "start": round(seg["start"] + offset_seconds, 2),
            "end": round(seg["end"] + offset_seconds, 2),
            "text": seg["text"],
        })
    return out


def segments_from_response(data: dict) -> tuple[list[dict], bool]:
    """Convierte la respuesta en segmentos {start, end, text}.
    Devuelve (segmentos, con_timestamps). Sin `segments` en la respuesta, el texto
    completo se devuelve como un unico segmento con start/end = None."""
    out: list[dict] = []
    for seg in data.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        try:
            start = round(float(seg.get("start")), 2)
            end = round(float(seg.get("end")), 2)
        except (TypeError, ValueError):
            start, end = None, None
        out.append({"start": start, "end": end, "text": text})
    if out:
        return out, all(s["start"] is not None for s in out)
    full = (data.get("text") or "").strip()
    if full:
        return [{"start": None, "end": None, "text": full}], False
    return [], False


def transcribe_chunks(chunks: list[tuple[Path, float, float]], transcribe_one) -> tuple[list[dict], list[dict], bool]:
    """Transcribe cada tramo y desplaza sus segmentos. Un tramo que falla se
    registra en `failed` con su rango; solo si fallan todos se aborta.
    Devuelve (segmentos, tramos_fallidos, todos_con_timestamps)."""
    segments: list[dict] = []
    failed: list[dict] = []
    all_ts = True
    for index, (path, offset, duration) in enumerate(chunks):
        try:
            chunk_segments, with_ts = transcribe_one(path)
        except SystemExit as exc:
            failed.append({"start": offset, "end": offset + duration, "motivo": str(exc)})
            print(f"[watch] tramo {index + 1}/{len(chunks)} fallo, se registra como no transcrito ({exc})",
                  file=sys.stderr)
            continue
        all_ts = all_ts and with_ts
        segments.extend(shift_segments(chunk_segments, offset))
        print(f"[watch] tramo {index + 1}/{len(chunks)}: {len(chunk_segments)} segmentos", file=sys.stderr)
    if chunks and len(failed) == len(chunks):
        raise SystemExit("la transcripcion fallo en todos los tramos de audio")
    return segments, failed, all_ts


def transcribe_video(
    video_path: str,
    audio_out: Path,
    provider: str,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
) -> dict:
    """Flujo completo: extraer audio -> subir -> segmentos.

    Devuelve un dict con: provider, model, segments, with_timestamps,
    failed_ranges (tramos no transcritos), audio_seconds, chunks.
    Lanza SystemExit ante cualquier falla total."""
    spec = provider_spec(provider)
    if not api_key:
        raise SystemExit(
            f"transcripcion con {provider}: falta la credencial. Este modulo no la busca; "
            "watch.py la resuelve por nombre (variable de entorno o --env-file) sin imprimirla."
        )
    model = model or spec["default_model"]
    timestamps = model in spec["models_with_timestamps"]
    if not timestamps:
        print(f"[watch] el modelo {model} no devuelve timestamps: la transcripcion saldra sin tiempos",
              file=sys.stderr)

    print(f"[watch] extrayendo audio para transcripcion ({provider}, {model})...", file=sys.stderr)
    audio_path = extract_audio(video_path, audio_out)
    audio_bytes = audio_path.stat().st_size
    duration = audio_duration(audio_path)

    def transcribe_one(path: Path) -> tuple[list[dict], bool]:
        response = _post_transcription(spec["endpoint"], api_key, model, path, language, timestamps)
        return segments_from_response(response)

    failed: list[dict] = []
    chunk_count = 1
    if audio_bytes <= MAX_UPLOAD_BYTES:
        print(f"[watch] audio: {audio_bytes / 1024:.0f} kB, subiendo a {provider}...", file=sys.stderr)
        segments, with_ts = transcribe_one(audio_path)
    else:
        plan = plan_chunks(duration, audio_bytes, MAX_UPLOAD_BYTES)
        chunk_count = len(plan)
        print(f"[watch] audio: {audio_bytes / (1024 * 1024):.0f} MB supera el tope de subida, "
              f"se corta en {chunk_count} tramos...", file=sys.stderr)
        chunks = split_audio(audio_path, audio_out.parent / "chunks", plan)
        segments, failed, with_ts = transcribe_chunks(chunks, transcribe_one)

    if not segments:
        raise SystemExit("la API de transcripcion no devolvio texto")
    print(f"[watch] transcripcion: {len(segments)} segmentos via {provider}", file=sys.stderr)
    return {
        "provider": provider,
        "model": model,
        "segments": segments,
        "with_timestamps": bool(with_ts),
        "failed_ranges": failed,
        "audio_seconds": duration,
        "chunks": chunk_count,
    }


if __name__ == "__main__":
    print(
        "[watch] whisper.py no se ejecuta solo. La transcripcion se activa con "
        "`watch.py <fuente> --transcribe groq|openai`, con proveedor y credencial explicitos.",
        file=sys.stderr,
    )
    raise SystemExit(2)
