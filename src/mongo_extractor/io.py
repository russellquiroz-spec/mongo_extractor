from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import pandas as pd

Pipeline = List[Dict[str, Any]]
PathLike = Union[str, Path]


def resolve_pipeline_path(pipeline_file: PathLike) -> Path:
    """
    Normaliza la ruta de un pipeline .json. Acepta rutas absolutas y relativas;
    las relativas se resuelven contra el directorio de trabajo actual.
    """
    return Path(pipeline_file).expanduser().resolve()


def parse_pipeline_json(text: str) -> Tuple[Pipeline, Optional[str]]:
    """Parsea un pipeline en texto JSON y devuelve (pipeline, collection).

    Formatos aceptados:
      - Array: [{"$match": ...}, ...]
      - Objeto con metadata: {"collection": "x", "pipeline": [...]}
      - Objeto suelto (una etapa): {"$project": ...}
    """
    data = json.loads(text)

    collection: Optional[str] = None

    if isinstance(data, dict):
        if "pipeline" in data:
            collection = data.get("collection")
            data = data["pipeline"]
        else:
            data = [data]

    if not isinstance(data, list):
        raise ValueError(
            "El JSON debe ser un array, un objeto con 'collection'+'pipeline', "
            "o un objeto con una etapa."
        )
    return data, collection


def read_pipeline_file(pipeline_file: PathLike) -> Tuple[Pipeline, Optional[str]]:
    """Lee un archivo .json y devuelve (pipeline, collection).

    Acepta los mismos formatos que `parse_pipeline_json`.
    """
    path = resolve_pipeline_path(pipeline_file)
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo: {path}")
    if not path.is_file():
        raise ValueError(f"No es un archivo: {path}")

    return parse_pipeline_json(path.read_text(encoding="utf-8"))


def save_dataframe(
    df: pd.DataFrame,
    output_path: str,
    fmt: Literal["csv", "parquet"] = "parquet",
    index: bool = False,
) -> str:
    """
    Guarda DataFrame en CSV o Parquet.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        df.to_csv(path, index=index)
    elif fmt == "parquet":
        df.to_parquet(path, index=index)
    else:
        raise ValueError("fmt debe ser 'csv' o 'parquet'")

    return str(path.resolve())
