from contextlib import contextmanager

import pytest

import mongo_extractor.tunnel as tunnel_module
from mongo_extractor.types import MongoConfig, SSHTunnelParams, SSMTunnelParams


def _ssh_profile() -> MongoConfig:
    return MongoConfig(
        tunnel="ssh",
        db="transactions",
        uri_template="mongodb://{user}:{password}@localhost:27018/",
        user="u",
        password="p",
        warmup_s=0.0,
        ssh=SSHTunnelParams(
            host="h", port=22, user="u", pkey_path="/tmp/k.pem",
            local_port=27018, remote_host="r", remote_port=27017,
        ),
    )


def _ssm_profile() -> MongoConfig:
    return MongoConfig(
        tunnel="ssm",
        db="BNPL",
        uri_template="mongodb://{user}:{password}@localhost:27017/",
        user="u",
        password="p",
        warmup_s=0.0,
        ssm=SSMTunnelParams(
            aws_region="us-east-2", target="i-xxx", local_port=27017,
            remote_host="r", remote_port=27017, ssm_command="echo ssm",
        ),
    )


def test_open_tunnel_dispatches_ssh(monkeypatch) -> None:
    called = {}

    @contextmanager
    def fake_ssh(params):
        called["ssh"] = params
        yield type("F", (), {"local_bind_port": 27018})()

    @contextmanager
    def fake_ssm(params):
        called["ssm"] = params
        yield None

    monkeypatch.setattr(tunnel_module, "open_ssh_tunnel", fake_ssh)
    monkeypatch.setattr(tunnel_module, "open_ssm_tunnel", fake_ssm)

    with tunnel_module.open_tunnel(_ssh_profile()) as port:
        assert port == 27018

    assert "ssh" in called
    assert "ssm" not in called


def test_open_tunnel_dispatches_ssm(monkeypatch) -> None:
    called = {}

    @contextmanager
    def fake_ssh(params):
        called["ssh"] = params
        yield None

    @contextmanager
    def fake_ssm(params):
        called["ssm"] = params
        from mongo_extractor.tunnels.ssm import SSMTunnelHandle
        yield SSMTunnelHandle(session_id="sess-1", proc=None, local_port=27017)

    monkeypatch.setattr(tunnel_module, "open_ssh_tunnel", fake_ssh)
    monkeypatch.setattr(tunnel_module, "open_ssm_tunnel", fake_ssm)

    with tunnel_module.open_tunnel(_ssm_profile()) as port:
        assert port == 27017

    assert "ssm" in called
    assert "ssh" not in called


def test_open_tunnel_rejects_unknown_mode() -> None:
    cfg = MongoConfig(
        tunnel="other",  # type: ignore[arg-type]
        db="x", uri_template="mongodb://localhost/", user="u", password="p", warmup_s=0.0,
    )
    with pytest.raises(ValueError):
        with tunnel_module.open_tunnel(cfg):
            pass
