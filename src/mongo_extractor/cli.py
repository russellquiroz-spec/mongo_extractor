from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import typer

from mongo_extractor.extractor import extract_aggregate, list_profiles
from mongo_extractor.io import save_dataframe
from mongo_extractor.logging import configure_logging
from mongo_extractor.pipeline_runner import (
    DEFAULT_LIMIT,
    DEFAULT_PROFILE,
    apply_limit,
    default_event_printer,
    print_result,
    read_pipeline_file,
    run_pipeline_with_retries,
)

app = typer.Typer(add_completion=False)


@app.command()
def ls():
    """
    Lista perfiles disponibles.
    """
    configure_logging()
    for a in list_profiles():
        typer.echo(a)


@app.command()
def run(
    profile: str = typer.Option(..., help="Alias del perfil (ver con: mongo-extractor ls)"),
    collection: str = typer.Option(..., help="Nombre de la coleccion"),
    pipeline: str = typer.Option(
        "[]",
        help='Pipeline de agregacion como JSON. Ejemplo: \'[{"$limit": 5}]\'',
    ),
    out: str = typer.Option("./output/result.parquet", help="Ruta de salida"),
    fmt: str = typer.Option("parquet", help="csv|parquet"),
):
    """
    Ejecuta un aggregate y guarda el resultado a archivo.
    """
    configure_logging()
    df = extract_aggregate(profile=profile, collection=collection, pipeline=pipeline)
    out_path = save_dataframe(df, out, fmt=fmt)  # type: ignore[arg-type]
    typer.echo(f"OK -> {out_path}")


@app.command("run-file")
def run_file(
    pipeline_file: Path = typer.Argument(..., help="Ruta del archivo .json con el pipeline de agregacion."),
    collection: Optional[str] = typer.Option(
        None, help="Override de la coleccion. Si no se pasa, se lee del campo 'collection' en el .json."
    ),
    profile: str = typer.Option(DEFAULT_PROFILE, help="Perfil de conexion para mongo_extractor."),
    limit: int = typer.Option(DEFAULT_LIMIT, help="Limite de documentos para prueba rapida (agrega $limit al final)."),
    full: bool = typer.Option(False, help="Ejecuta el pipeline completo, sin agregar $limit."),
    retries: int = typer.Option(3, help="Intentos maximos si falla la conexion."),
    retry_wait: float = typer.Option(5.0, help="Segundos de espera entre reintentos de conexion."),
    output: Optional[Path] = typer.Option(None, help="Opcional: guarda el resultado en CSV."),
    print_pipeline: bool = typer.Option(False, "--print-pipeline", help="Imprime el pipeline final que se va a ejecutar."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo arma/imprime el pipeline final; no lo ejecuta."),
):
    """
    Ejecuta un pipeline de agregacion leido desde un archivo .json, con $limit y reintentos.
    """
    configure_logging()

    raw_pipeline, file_collection = read_pipeline_file(pipeline_file)
    resolved_collection = collection or file_collection
    if not resolved_collection:
        typer.echo(
            "ERROR - No se especifico coleccion. Agrega 'collection' en el .json o usa --collection.",
            err=True,
        )
        raise typer.Exit(code=1)

    final_limit = None if full else limit
    final_pipeline = apply_limit(raw_pipeline, final_limit)

    mode = "FULL" if full else f"$limit {limit}"
    typer.echo(f"Perfil: {profile}")
    typer.echo(f"Coleccion: {resolved_collection}")
    typer.echo(f"Archivo: {pipeline_file}")
    typer.echo(f"Modo: {mode}")
    typer.echo(f"Etapas: {len(final_pipeline)}")

    if print_pipeline:
        typer.echo("")
        typer.echo(json.dumps(final_pipeline, indent=2, ensure_ascii=False, default=str))
        typer.echo("")

    if dry_run:
        typer.echo("DRY RUN - No se ejecuto el pipeline.")
        return

    started_at = time.perf_counter()
    df = run_pipeline_with_retries(
        profile=profile,
        collection=resolved_collection,
        pipeline=final_pipeline,
        retries=retries,
        retry_wait=retry_wait,
        on_event=default_event_printer,
    )
    elapsed_seconds = time.perf_counter() - started_at

    print_result(df, elapsed_seconds)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output, index=False)
        typer.echo(f"\nCSV guardado en: {output}")
