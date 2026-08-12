from __future__ import annotations

import os
import time
from datetime import datetime as dt
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Union
from urllib.parse import quote_plus

import pandas as pd
from pymongo import MongoClient

from mongo_extractor.config import load_config
from mongo_extractor.io import parse_pipeline_json, read_pipeline_file, resolve_pipeline_path
from mongo_extractor.tunnel import open_tunnel
from mongo_extractor.types import MongoConfig

Pipeline = List[Dict[str, Any]]

Level = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
EventType = Literal[
    "CONFIG_LOADED",
    "PIPELINE_LOADED",
    "ALIAS_RESOLVED",
    "TUNNEL_START",
    "TUNNEL_READY",
    "DB_CONNECT_START",
    "DB_CONNECTED",
    "QUERY_START",
    "QUERY_OK",
    "CONNECTION_CLOSED",
    "DONE",
    "ERROR",
]

StatusEvent = Dict[str, Any]
OnEvent = Callable[[StatusEvent], None]


def _emit(
    on_event: Optional[OnEvent],
    *,
    level: Level,
    event: EventType,
    message: str,
    **fields: Any,
) -> None:
    if on_event is None:
        return
    payload: StatusEvent = {
        "ts": dt.now().isoformat(timespec="seconds"),
        "level": level,
        "event": event,
        "message": message,
        **fields,
    }
    on_event(payload)


def _render_uri(template: str, user: str, password: str) -> str:
    """
    Reemplaza {user} y {password} en el template con URL-encoding.
    """
    return template.replace("{user}", quote_plus(user)).replace("{password}", quote_plus(password))


def list_available_profiles(profiles: Dict[str, MongoConfig]) -> List[str]:
    return sorted(profiles.keys())


def list_profiles(*, on_event: Optional[OnEvent] = None) -> List[str]:
    """
    Lista aliases (perfiles) disponibles, normalizados a lowercase.
    """
    app, profiles = load_config()
    _emit(
        on_event,
        level="INFO",
        event="CONFIG_LOADED",
        message="Config loaded.",
        profiles=len(profiles),
        server_selection_timeout_ms=app.server_selection_timeout_ms,
    )
    return list_available_profiles(profiles)


def extract_aggregate(
    profile: str,
    collection: Optional[str] = None,
    pipeline: Optional[Union[Pipeline, str]] = None,
    *,
    pipeline_file: Optional[Union[str, Path]] = None,
    on_event: Optional[OnEvent] = None,
    save_dir: Optional[str] = None,
    base_name: Optional[str] = None,
    save_csv: bool = False,
    save_parquet: bool = False,
    csv_index: bool = False,
    csv_encoding: str = "utf-8",
    parquet_index: bool = False,
    allow_disk_use: bool = True,
) -> pd.DataFrame:
    """
    Ejecuta una agregacion Mongo en `profile`.collection y devuelve un DataFrame.

    El pipeline se pasa de una de dos formas:
      - pipeline: lista de etapas en memoria, o el mismo JSON como texto
      - pipeline_file: ruta a un .json (absoluta o relativa al cwd)

    Si llegan las dos, `pipeline` gana y `pipeline_file` se ignora (no se lee).

    En texto y en archivo, si el JSON trae el campo 'collection' se usa cuando no
    se pasa `collection`.

    Persistencia opcional:
      - save_dir: carpeta destino (si None, no guarda nada)
      - base_name: nombre base (sin extension). Si None, genera uno.
      - save_csv: guardar CSV
      - save_parquet: guardar Parquet
    """
    started = dt.now()
    profile_in = profile
    profile = profile.lower()

    from_json = isinstance(pipeline, str) or (pipeline is None and pipeline_file is not None)

    if isinstance(pipeline, str):
        pipeline, text_collection = parse_pipeline_json(pipeline)
        collection = collection or text_collection
        _emit(
            on_event,
            level="INFO",
            event="PIPELINE_LOADED",
            message="Pipeline parsed from text.",
            profile=profile,
            source="text",
            stages=len(pipeline),
            collection=collection,
        )
    elif pipeline is not None:
        pass
    elif pipeline_file is not None:
        resolved_file = resolve_pipeline_path(pipeline_file)
        pipeline, file_collection = read_pipeline_file(resolved_file)
        collection = collection or file_collection
        _emit(
            on_event,
            level="INFO",
            event="PIPELINE_LOADED",
            message="Pipeline loaded from file.",
            profile=profile,
            source="file",
            path=str(resolved_file),
            stages=len(pipeline),
            collection=collection,
        )
    else:
        _emit(
            on_event,
            level="ERROR",
            event="ERROR",
            message="Missing pipeline.",
            profile=profile,
        )
        raise ValueError("Debes pasar pipeline o pipeline_file")

    if not isinstance(collection, str) or not collection.strip():
        _emit(on_event, level="ERROR", event="ERROR", message="Empty collection.", profile=profile)
        if from_json:
            raise ValueError(
                "No se especifico coleccion. Agrega 'collection' en el JSON o pasa collection=..."
            )
        raise ValueError("collection debe ser un string no vacio")

    if not isinstance(pipeline, list) or not all(isinstance(x, dict) for x in pipeline):
        _emit(
            on_event,
            level="ERROR",
            event="ERROR",
            message="Invalid pipeline.",
            profile=profile,
            collection=collection,
        )
        raise ValueError("pipeline debe ser una lista de dicts")

    _emit(
        on_event,
        level="INFO",
        event="ALIAS_RESOLVED",
        message="Resolving profile alias.",
        profile_input=profile_in,
        profile=profile,
    )

    app, profiles = load_config()
    _emit(
        on_event,
        level="INFO",
        event="CONFIG_LOADED",
        message="Config loaded.",
        profiles=len(profiles),
        server_selection_timeout_ms=app.server_selection_timeout_ms,
    )

    if profile not in profiles:
        available = sorted(profiles.keys())
        _emit(
            on_event,
            level="ERROR",
            event="ERROR",
            message="Profile alias not found.",
            profile=profile,
            available=available,
        )
        raise ValueError(f"Profile '{profile}' no existe. Disponibles: {', '.join(available)}")

    cfg = profiles[profile]

    want_save = bool(save_dir) and (save_csv or save_parquet)
    csv_path = None
    pq_path = None
    if want_save:
        out_dir = Path(save_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        if base_name:
            bn = base_name
        else:
            ts = dt.now().strftime("%Y%m%d_%H%M%S")
            bn = f"{profile}_{cfg.db}_{collection}_{ts}"
        csv_path = out_dir / f"{bn}.csv"
        pq_path = out_dir / f"{bn}.parquet"

    client: Optional[MongoClient] = None

    try:
        _emit(
            on_event,
            level="INFO",
            event="TUNNEL_START",
            message="Opening tunnel.",
            profile=profile,
            tunnel=cfg.tunnel,
            warmup_s=cfg.warmup_s,
        )

        with open_tunnel(cfg) as local_port:
            if cfg.warmup_s > 0:
                time.sleep(cfg.warmup_s)

            _emit(
                on_event,
                level="INFO",
                event="TUNNEL_READY",
                message="Tunnel ready.",
                profile=profile,
                tunnel=cfg.tunnel,
                local_port=local_port,
            )

            uri = _render_uri(cfg.uri_template, cfg.user, cfg.password)

            _emit(
                on_event,
                level="INFO",
                event="DB_CONNECT_START",
                message="Connecting to MongoDB.",
                profile=profile,
                db=cfg.db,
            )

            client = MongoClient(uri, serverSelectionTimeoutMS=app.server_selection_timeout_ms)
            client.admin.command("ping")

            _emit(
                on_event,
                level="INFO",
                event="DB_CONNECTED",
                message="Connected to MongoDB.",
                profile=profile,
                db=cfg.db,
            )

            db = client[cfg.db]
            coll = db[collection]

            _emit(
                on_event,
                level="INFO",
                event="QUERY_START",
                message="Executing aggregate pipeline.",
                profile=profile,
                db=cfg.db,
                collection=collection,
                stages=len(pipeline),
            )

            cursor = coll.aggregate(pipeline, allowDiskUse=allow_disk_use)
            records = list(cursor)
            df = pd.DataFrame(records)

            _emit(
                on_event,
                level="INFO",
                event="QUERY_OK",
                message="Aggregate executed successfully.",
                profile=profile,
                rows=int(len(df)),
                cols=int(len(df.columns)),
            )

            if want_save:
                if save_csv and csv_path is not None:
                    df.to_csv(csv_path, index=csv_index, encoding=csv_encoding)
                    _emit(
                        on_event,
                        level="INFO",
                        event="QUERY_OK",
                        message="CSV saved.",
                        path=str(csv_path),
                        bytes=int(os.path.getsize(csv_path)),
                    )
                if save_parquet and pq_path is not None:
                    df.to_parquet(pq_path, index=parquet_index)
                    _emit(
                        on_event,
                        level="INFO",
                        event="QUERY_OK",
                        message="Parquet saved.",
                        path=str(pq_path),
                        bytes=int(os.path.getsize(pq_path)),
                    )

            return df

    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        _emit(
            on_event,
            level="ERROR",
            event="ERROR",
            message=f"{type(exc).__name__}: {exc}",
            profile=profile,
        )
        raise RuntimeError(f"Error en extract_aggregate('{profile}'): {exc}") from exc

    finally:
        try:
            if client is not None:
                client.close()
                _emit(
                    on_event,
                    level="DEBUG",
                    event="CONNECTION_CLOSED",
                    message="Mongo client closed.",
                    profile=profile,
                )
        except Exception:
            pass

        ended = dt.now()
        _emit(
            on_event,
            level="INFO",
            event="DONE",
            message="Extraction finished.",
            profile=profile,
            elapsed_s=round((ended - started).total_seconds(), 3),
        )
