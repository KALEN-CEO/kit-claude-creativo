#!/usr/bin/env python3
"""Resolucion de credenciales por nombre, sin exponer valores.

Unico modulo de `/watch` autorizado a consultar el entorno o un archivo de
entorno. Contrato:

- Solo se importa cuando la transcripcion se activa de forma explicita
  (`watch.py --transcribe groq`). El camino por defecto de `/watch` no lo
  importa.
- Busca UNICAMENTE los nombres que recibe. No enumera el entorno ni el archivo,
  no devuelve otras claves, no imprime valores y no los escribe en ningun lado.
- Orden: variable de entorno del proceso; despues, si se indico un archivo de
  entorno, la linea `NOMBRE=valor` de ese archivo. El archivo no se crea, no se
  modifica y no se copia.
- Devuelve el valor junto con una etiqueta de procedencia ("entorno" o
  "archivo de entorno") para que el informe declare de donde salio SIN
  reproducirlo. `describe()` sirve para preflights: solo presente/ausente.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")

SOURCE_ENV = "entorno"
SOURCE_FILE = "archivo de entorno"


class SecretLookup:
    """Resultado de una busqueda. `value` nunca debe imprimirse ni loguearse."""

    __slots__ = ("name", "value", "source")

    def __init__(self, name: str, value: str | None, source: str | None):
        self.name = name
        self.value = value
        self.source = source

    @property
    def present(self) -> bool:
        return bool(self.value)

    def __repr__(self) -> str:  # nunca revela el valor
        state = f"presente via {self.source}" if self.present else "ausente"
        return f"SecretLookup({self.name}: {state})"


def _strip_quotes(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    # valor sin comillas: cortar comentario inline
    if " #" in raw:
        raw = raw.split(" #", 1)[0].rstrip()
    return raw


def _from_env_file(path: Path, wanted: set[str]) -> dict[str, str]:
    """Lee solo las claves `wanted` del archivo. Ignora todo lo demas."""
    found: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return found
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        if name in wanted and name not in found:
            value = _strip_quotes(m.group(2))
            if value:
                found[name] = value
    return found


def resolve_secret(names: list[str] | tuple[str, ...], env_file: str | Path | None = None) -> SecretLookup:
    """Primer nombre de `names` presente en el entorno del proceso; si no hay
    ninguno y `env_file` existe, primer nombre presente en ese archivo."""
    names = [n for n in names if n]
    for name in names:
        value = os.environ.get(name)
        if value:
            return SecretLookup(name, value, SOURCE_ENV)
    if env_file:
        path = Path(env_file).expanduser()
        if path.is_file():
            found = _from_env_file(path, set(names))
            for name in names:
                if name in found:
                    return SecretLookup(name, found[name], SOURCE_FILE)
    return SecretLookup(names[0] if names else "", None, None)


def describe(names: list[str] | tuple[str, ...], env_file: str | Path | None = None) -> dict[str, str]:
    """Para preflights: {nombre: 'presente (entorno)' | 'presente (archivo de entorno)' | 'ausente'}.
    Nunca incluye valores."""
    out: dict[str, str] = {}
    for name in names:
        hit = resolve_secret([name], env_file)
        out[name] = f"presente ({hit.source})" if hit.present else "ausente"
    return out
