import json

import pytest

import mongo_extractor.extractor as extractor
from mongo_extractor import extract_aggregate


class _StopAfterLoad(Exception):
    pass


@pytest.fixture
def stop_before_connect(monkeypatch):
    """Corta la ejecucion justo despues de resolver pipeline/collection."""

    def fake_load_config():
        raise _StopAfterLoad()

    monkeypatch.setattr(extractor, "load_config", fake_load_config)


def _write_pipeline(tmp_path, name, data):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _events_of(events, event_type):
    return [e for e in events if e["event"] == event_type]


def test_pipeline_file_absolute_path(tmp_path, stop_before_connect) -> None:
    path = _write_pipeline(
        tmp_path, "queries/mi_pipeline.json", {"collection": "users", "pipeline": [{"$limit": 3}]}
    )
    events = []

    with pytest.raises(_StopAfterLoad):
        extract_aggregate(profile="bnpl", pipeline_file=path, on_event=events.append)

    loaded = _events_of(events, "PIPELINE_LOADED")[0]
    assert loaded["path"] == str(path.resolve())
    assert loaded["collection"] == "users"
    assert loaded["stages"] == 1


def test_pipeline_file_relative_path_resolves_against_cwd(tmp_path, monkeypatch, stop_before_connect) -> None:
    path = _write_pipeline(
        tmp_path, "queries/mi_pipeline.json", {"collection": "users", "pipeline": [{"$limit": 3}]}
    )
    monkeypatch.chdir(tmp_path)
    events = []

    with pytest.raises(_StopAfterLoad):
        extract_aggregate(profile="bnpl", pipeline_file="queries/mi_pipeline.json", on_event=events.append)

    assert _events_of(events, "PIPELINE_LOADED")[0]["path"] == str(path.resolve())


def test_collection_argument_overrides_file_collection(tmp_path, stop_before_connect) -> None:
    path = _write_pipeline(tmp_path, "p.json", {"collection": "users", "pipeline": [{"$limit": 3}]})
    events = []

    with pytest.raises(_StopAfterLoad):
        extract_aggregate("bnpl", "otra_coll", pipeline_file=path, on_event=events.append)

    assert _events_of(events, "PIPELINE_LOADED")[0]["collection"] == "otra_coll"


def test_pipeline_as_json_text(stop_before_connect) -> None:
    events = []

    with pytest.raises(_StopAfterLoad):
        extract_aggregate(
            profile="bnpl",
            pipeline='{"collection": "users", "pipeline": [{"$limit": 3}]}',
            on_event=events.append,
        )

    loaded = _events_of(events, "PIPELINE_LOADED")[0]
    assert loaded["source"] == "text"
    assert loaded["collection"] == "users"
    assert loaded["stages"] == 1


def test_pipeline_as_json_text_array(stop_before_connect) -> None:
    events = []

    with pytest.raises(_StopAfterLoad):
        extract_aggregate("bnpl", "users", '[{"$match": {"a": 1}}, {"$limit": 2}]', on_event=events.append)

    assert _events_of(events, "PIPELINE_LOADED")[0]["stages"] == 2


def test_pipeline_as_invalid_json_text_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        extract_aggregate("bnpl", "users", "[{$limit: 2}]")


def test_pipeline_as_json_text_without_collection_raises() -> None:
    with pytest.raises(ValueError, match="No se especifico coleccion"):
        extract_aggregate("bnpl", pipeline='[{"$limit": 1}]')


def test_pipeline_file_without_collection_raises(tmp_path) -> None:
    path = _write_pipeline(tmp_path, "arr.json", [{"$limit": 1}])

    with pytest.raises(ValueError, match="No se especifico coleccion"):
        extract_aggregate("bnpl", pipeline_file=path)


def test_pipeline_and_pipeline_file_are_mutually_exclusive(tmp_path) -> None:
    path = _write_pipeline(tmp_path, "p.json", [{"$limit": 1}])

    with pytest.raises(ValueError, match="no ambos"):
        extract_aggregate("bnpl", "users", [{"$limit": 1}], pipeline_file=path)


def test_missing_pipeline_and_pipeline_file_raises() -> None:
    with pytest.raises(ValueError, match="Debes pasar pipeline o pipeline_file"):
        extract_aggregate("bnpl", "users")


def test_pipeline_file_not_found_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_aggregate("bnpl", "users", pipeline_file=tmp_path / "no_existe.json")
