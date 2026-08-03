from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd


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
