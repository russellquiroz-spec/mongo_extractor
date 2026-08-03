import json

import pandas as pd
import pytest

import mongo_extractor.pipeline_runner as pipeline_runner
from mongo_extractor.pipeline_runner import (
    apply_limit,
    is_connection_error,
    read_pipeline_file,
    run_pipeline_with_retries,
)


def _write_json(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_read_pipeline_file_array_requires_external_collection(tmp_path) -> None:
    path = _write_json(tmp_path, "array.json", [{"$match": {"status": "active"}}])

    pipeline, collection = read_pipeline_file(path)

    assert pipeline == [{"$match": {"status": "active"}}]
    assert collection is None


def test_read_pipeline_file_object_with_collection_and_pipeline(tmp_path) -> None:
    data = {
        "collection": "products",
        "pipeline": [{"$project": {"_id": 0, "name": 1}}],
    }
    path = _write_json(tmp_path, "with_collection.json", data)

    pipeline, collection = read_pipeline_file(path)

    assert pipeline == [{"$project": {"_id": 0, "name": 1}}]
    assert collection == "products"


def test_read_pipeline_file_single_stage_object(tmp_path) -> None:
    path = _write_json(tmp_path, "single_stage.json", {"$project": {"_id": 0, "name": 1}})

    pipeline, collection = read_pipeline_file(path)

    assert pipeline == [{"$project": {"_id": 0, "name": 1}}]
    assert collection is None


def test_read_pipeline_file_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        read_pipeline_file(tmp_path / "no_existe.json")


def test_apply_limit_none_returns_pipeline_unchanged() -> None:
    pipeline = [{"$match": {"status": "active"}}]
    assert apply_limit(pipeline, None) == pipeline


def test_apply_limit_appends_limit_stage() -> None:
    pipeline = [{"$match": {"status": "active"}}]
    assert apply_limit(pipeline, 10) == pipeline + [{"$limit": 10}]


def test_apply_limit_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        apply_limit([], 0)


@pytest.mark.parametrize(
    "error",
    [
        Exception("ServerSelectionTimeoutError: could not establish connection"),
        Exception("SSH tunnel could not be established"),
        TimeoutError("connection timed out"),
    ],
)
def test_is_connection_error_true_for_connection_issues(error) -> None:
    assert is_connection_error(error) is True


@pytest.mark.parametrize(
    "error",
    [
        Exception("OperationFailure: unknown operator '$foo'"),
        ValueError("collection debe ser un string no vacio"),
    ],
)
def test_is_connection_error_false_for_pipeline_issues(error) -> None:
    assert is_connection_error(error) is False


def test_run_pipeline_with_retries_rejects_non_positive_retries() -> None:
    with pytest.raises(ValueError):
        run_pipeline_with_retries(profile="tx", collection="x", pipeline=[], retries=0)


def test_run_pipeline_with_retries_retries_on_connection_error_then_succeeds(monkeypatch) -> None:
    calls = {"n": 0}
    sleeps = []

    def fake_extract_aggregate(*, profile, collection, pipeline, on_event=None):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("ServerSelectionTimeoutError: could not establish connection")
        return pd.DataFrame([{"a": 1}])

    monkeypatch.setattr(pipeline_runner, "extract_aggregate", fake_extract_aggregate)
    monkeypatch.setattr(pipeline_runner.time, "sleep", lambda s: sleeps.append(s))

    df = run_pipeline_with_retries(
        profile="tx", collection="x", pipeline=[{"$limit": 1}], retries=3, retry_wait=1.0
    )

    assert calls["n"] == 2
    assert sleeps == [1.0]
    assert df.equals(pd.DataFrame([{"a": 1}]))


def test_run_pipeline_with_retries_does_not_retry_on_pipeline_error(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_extract_aggregate(*, profile, collection, pipeline, on_event=None):
        calls["n"] += 1
        raise RuntimeError("OperationFailure: unknown operator '$foo'")

    monkeypatch.setattr(pipeline_runner, "extract_aggregate", fake_extract_aggregate)

    with pytest.raises(RuntimeError, match="unknown operator"):
        run_pipeline_with_retries(profile="tx", collection="x", pipeline=[], retries=3, retry_wait=0)

    assert calls["n"] == 1


def test_run_pipeline_with_retries_gives_up_after_max_attempts(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_extract_aggregate(*, profile, collection, pipeline, on_event=None):
        calls["n"] += 1
        raise RuntimeError("connection timed out")

    monkeypatch.setattr(pipeline_runner, "extract_aggregate", fake_extract_aggregate)
    monkeypatch.setattr(pipeline_runner.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="timed out"):
        run_pipeline_with_retries(profile="tx", collection="x", pipeline=[], retries=2, retry_wait=0)

    assert calls["n"] == 2
