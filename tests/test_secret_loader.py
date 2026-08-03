import sys
import types

from mongo_extractor.secret_loader import parse_credentials_secret, resolve_secret_reference


def test_resolve_secret_reference_reads_keyring_manager_entry(tmp_path, monkeypatch) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    keyring_dir = appdata / "KeyringManager"
    keyring_dir.mkdir(parents=True)
    (keyring_dir / "credentials.json").write_text(
        '[{"env_var":"RIM_FINTECH_BNPL_KEY","usuario":"bnpl_user","service":"BNPL Mongo"}]',
        encoding="utf-8",
    )

    fake_keyring = types.SimpleNamespace(
        get_password=lambda service, user: "bnpl_secret"
        if service == "BNPL Mongo" and user == "bnpl_user"
        else None
    )

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

    user, password = resolve_secret_reference("RIM_FINTECH_BNPL_KEY")

    assert user == "bnpl_user"
    assert password == "bnpl_secret"


def test_parse_credentials_secret_reads_escaped_json_payload() -> None:
    credentials = parse_credentials_secret(
        '{\\"user\\":\\"bnpl_user\\",\\"password\\":\\"bnpl_secret\\",\\"note\\":\\"ok\\"}'
    )

    assert credentials == ("bnpl_user", "bnpl_secret")
