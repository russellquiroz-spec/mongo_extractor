import os

from mongo_extractor.config import load_config


def _clear_config_env(monkeypatch) -> None:
    managed_keys = {
        "LOG_LEVEL",
        "OUTPUT_DIR",
        "MONGO_SERVER_SELECTION_TIMEOUT_MS",
        "MONGO_EXTRACTOR_ENV_FILE",
        "RIM_FINTECH_BNPL_KEY",
    }
    for key in list(os.environ):
        if key.startswith("MONGO__") or key in managed_keys:
            monkeypatch.delenv(key, raising=False)


def _write_env(tmp_path) -> str:
    env_file = tmp_path / ".env.mongo_extractor"
    env_file.write_text(
        "\n".join(
            [
                "MONGO__bnpl__TUNNEL=ssm",
                "MONGO__bnpl__DB=BNPL",
                "MONGO__bnpl__URI=mongodb://{user}:{password}@localhost:27017/?authSource=admin",
                "MONGO__bnpl__CREDENTIALS_ENV=RIM_FINTECH_BNPL_KEY",
                "MONGO__bnpl__WARMUP_S=10",
                "MONGO__bnpl__AWS_REGION=us-east-2",
                "MONGO__bnpl__SSM_TARGET=i-0d9002794c9ad3b62",
                "MONGO__bnpl__LOCAL_PORT=27017",
                "MONGO__bnpl__REMOTE_HOST=cluster-bnpl.docdb.amazonaws.com",
                "MONGO__bnpl__REMOTE_PORT=27017",
                "MONGO__bnpl__SSM_COMMAND=aws ssm start-session --target i-xxx",
                "MONGO__tx__TUNNEL=ssh",
                "MONGO__tx__DB=transactions",
                "MONGO__tx__URI=mongodb://{user}:{password}@localhost:27018/?authSource=admin",
                "MONGO__tx__CREDENTIALS_ENV=RIM_FINTECH_BNPL_KEY",
                "MONGO__tx__SSH_HOST=jump-host.example.com",
                "MONGO__tx__SSH_PORT=22",
                "MONGO__tx__SSH_USER=ec2-user",
                "MONGO__tx__SSH_KEY_PATH=/tmp/k.pem",
                "MONGO__tx__LOCAL_PORT=27018",
                "MONGO__tx__REMOTE_HOST=cluster-tx.docdb.amazonaws.com",
                "MONGO__tx__REMOTE_PORT=27017",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return str(env_file)


def test_load_config_reads_credentials_from_system_env_for_both_profiles(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_path = _write_env(tmp_path)
    monkeypatch.setenv("MONGO_EXTRACTOR_ENV_FILE", env_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "NoKeyring"))
    monkeypatch.setenv(
        "RIM_FINTECH_BNPL_KEY",
        '{"user":"shared_user","password":"shared_secret"}',
    )

    app, profiles = load_config()

    assert app.server_selection_timeout_ms == 20000
    assert set(profiles.keys()) == {"bnpl", "tx"}

    bnpl = profiles["bnpl"]
    assert bnpl.tunnel == "ssm"
    assert bnpl.db == "BNPL"
    assert bnpl.user == "shared_user"
    assert bnpl.password == "shared_secret"
    assert bnpl.warmup_s == 10.0
    assert bnpl.ssm is not None
    assert bnpl.ssh is None
    assert bnpl.ssm.target == "i-0d9002794c9ad3b62"

    tx = profiles["tx"]
    assert tx.tunnel == "ssh"
    assert tx.db == "transactions"
    assert tx.warmup_s == 1.0  # default cuando no se especifica
    assert tx.ssh is not None
    assert tx.ssm is None
    assert tx.ssh.host == "jump-host.example.com"
    assert tx.ssh.local_port == 27018


def test_load_config_rejects_invalid_tunnel_mode(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.mongo_extractor"
    env_file.write_text(
        "\n".join(
            [
                "MONGO__bad__TUNNEL=httpx",
                "MONGO__bad__DB=foo",
                "MONGO__bad__URI=mongodb://localhost:27017/",
                "MONGO__bad__CREDENTIALS_ENV=RIM_FINTECH_BNPL_KEY",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MONGO_EXTRACTOR_ENV_FILE", str(env_file))
    monkeypatch.setenv("APPDATA", str(tmp_path / "NoKeyring"))
    monkeypatch.setenv("RIM_FINTECH_BNPL_KEY", "u:p")

    try:
        load_config()
    except ValueError as exc:
        assert "TUNNEL invalido" in str(exc)
        return
    raise AssertionError("Esperaba ValueError por TUNNEL invalido")


def test_load_config_rejects_ssm_profile_with_missing_fields(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.mongo_extractor"
    env_file.write_text(
        "\n".join(
            [
                "MONGO__bnpl__TUNNEL=ssm",
                "MONGO__bnpl__DB=BNPL",
                "MONGO__bnpl__URI=mongodb://{user}:{password}@localhost:27017/",
                "MONGO__bnpl__CREDENTIALS_ENV=RIM_FINTECH_BNPL_KEY",
                # falta AWS_REGION, SSM_TARGET, etc.
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MONGO_EXTRACTOR_ENV_FILE", str(env_file))
    monkeypatch.setenv("APPDATA", str(tmp_path / "NoKeyring"))
    monkeypatch.setenv("RIM_FINTECH_BNPL_KEY", "u:p")

    try:
        load_config()
    except ValueError as exc:
        assert "SSM incompleta" in str(exc)
        return
    raise AssertionError("Esperaba ValueError por SSM incompleta")
