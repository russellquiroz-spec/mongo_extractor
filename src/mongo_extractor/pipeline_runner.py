from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from mongo_extractor.extractor import OnEvent, extract_aggregate
from mongo_extractor.io import read_pipeline_file, resolve_pipeline_path  # noqa: F401  (re-export)

Pipeline = List[Dict[str, Any]]

DEFAULT_PROFILE = "tx"
DEFAULT_LIMIT = 10

CONNECTION_ERROR_HINTS = (
    "could not establish connection",
    "connection refused",
    "connection reset",
    "connection timed out",
    "server closed the connection",
    "ssh",
    "tunnel",
    "timeout",
    "timed out",
    "operationalerror",
    "serverselectiontimeouterror",
)


def apply_limit(pipeline: Pipeline, limit: Optional[int]) -> Pipeline:
    if limit is None:
        return pipeline
    if limit <= 0:
        raise ValueError("limit debe ser mayor a 0. Usa full=True si no quieres limite.")
    return pipeline + [{"$limit": limit}]


def is_connection_error(error: Exception) -> bool:
    message = f"{type(error).__name__}: {error}".lower()
    return any(hint in message for hint in CONNECTION_ERROR_HINTS)


def default_event_printer(evt: Dict[str, Any]) -> None:
    extras = {k: v for k, v in evt.items() if k not in ("ts", "level", "event", "message")}
    extra_str = f" | {extras}" if extras else ""
    print(f'{evt.get("ts", "")} [{evt.get("level", "INFO")}] {evt.get("event", "")}: {evt.get("message", "")}{extra_str}')


def print_result(df: pd.DataFrame, elapsed_seconds: float) -> None:
    print(f"\nOK - Pipeline ejecutado en {elapsed_seconds:.1f}s")

    shape = getattr(df, "shape", None)
    if shape and len(shape) == 2:
        rows, cols = shape
        print(f"Documentos: {rows:,}")
        print(f"Columnas: {cols:,}")

    if hasattr(df, "head"):
        print("")
        print(df.head(DEFAULT_LIMIT).to_string(index=False))


def run_pipeline_with_retries(
    *,
    profile: str,
    collection: str,
    pipeline: Pipeline,
    retries: int = 3,
    retry_wait: float = 5.0,
    on_event: Optional[OnEvent] = None,
) -> pd.DataFrame:
    """
    Ejecuta `pipeline` (ya armado, con $limit ya aplicado si aplica) via `extract_aggregate`,
    reintentando solo ante errores de conexion/tunel.
    """
    if retries <= 0:
        raise ValueError("retries debe ser mayor a 0.")

    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            if attempt > 1:
                print(f"Reintento {attempt}/{retries}...")
            return extract_aggregate(
                profile=profile,
                collection=collection,
                pipeline=pipeline,
                on_event=on_event,
            )
        except Exception as error:
            last_error = error
            if not is_connection_error(error):
                raise
            if attempt == retries:
                break
            print(f"Fallo de conexion. Esperando {retry_wait:.1f}s antes de reintentar...")
            time.sleep(retry_wait)

    assert last_error is not None
    raise last_error


def run_pipeline_from_file(
    pipeline_file: Path,
    *,
    collection: Optional[str] = None,
    profile: str = DEFAULT_PROFILE,
    limit: Optional[int] = DEFAULT_LIMIT,
    full: bool = False,
    retries: int = 3,
    retry_wait: float = 5.0,
    on_event: Optional[OnEvent] = None,
) -> pd.DataFrame:
    """
    Lee un pipeline desde `pipeline_file`, aplica $limit (salvo full=True) y lo ejecuta
    con `extract_aggregate`, reintentando ante errores de conexion/tunel.

    Wrapper de conveniencia para uso como libreria. El CLI y el runner de script usan
    `read_pipeline_file` + `apply_limit` + `run_pipeline_with_retries` directamente para
    poder imprimir el pipeline final antes de ejecutarlo (--print-pipeline/--dry-run).
    """
    raw_pipeline, file_collection = read_pipeline_file(pipeline_file)
    resolved_collection = collection or file_collection
    if not resolved_collection:
        raise ValueError(
            "No se especifico coleccion. Agrega 'collection' en el .json o pasa collection=..."
        )

    final_limit = None if full else limit
    final_pipeline = apply_limit(raw_pipeline, final_limit)

    return run_pipeline_with_retries(
        profile=profile,
        collection=resolved_collection,
        pipeline=final_pipeline,
        retries=retries,
        retry_wait=retry_wait,
        on_event=on_event,
    )
