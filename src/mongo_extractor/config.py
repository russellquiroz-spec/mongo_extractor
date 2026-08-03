from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Tuple

from dotenv import load_dotenv

import mongo_extractor.secret_loader as _secret_loader
from mongo_extractor.types import (
    AppConfig,
    MongoConfig,
    SSHTunnelParams,
    SSMTunnelParams,
)

_MONGO_KEY_RE = re.compile(r"^MONGO__(?P<alias>[A-Za-z0-9_-]+)__(?P<field>[A-Z_]+)$")
_REQUIRED_COMMON_FIELDS = {"TUNNEL", "DB", "URI"}
_REQUIRED_SSH_FIELDS = {"SSH_HOST", "SSH_USER", "SSH_KEY_PATH", "LOCAL_PORT", "REMOTE_HOST", "REMOTE_PORT"}
_REQUIRED_SSM_FIELDS = {"AWS_REGION", "SSM_TARGET", "LOCAL_PORT", "REMOTE_HOST", "REMOTE_PORT", "SSM_COMMAND"}
_CREDENTIAL_ENV_FIELD = "CREDENTIALS_ENV"


def _find_env_file() -> Path:
    """
    Encuentra .env.mongo_extractor sin depender del cwd del notebook.

    Orden:
    1) MONGO_EXTRACTOR_ENV_FILE (si esta seteado)
    2) Busca hacia arriba desde el directorio del paquete hasta 8 niveles
       (cubre editable installs: <repo>/src/mongo_extractor/*.py)
    """
    override = os.getenv("MONGO_EXTRACTOR_ENV_FILE")
    if override:
        path = Path(override).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"MONGO_EXTRACTOR_ENV_FILE apunta a un archivo inexistente: {path}")
        return path

    current = Path(__file__).resolve().parent
    for _ in range(8):
        candidate = current / ".env.mongo_extractor"
        if candidate.exists():
            return candidate
        current = current.parent

    raise FileNotFoundError(
        "No se encontro .env.mongo_extractor.\n"
        "Colocalo en la raiz del repo o define MONGO_EXTRACTOR_ENV_FILE con ruta absoluta."
    )


def _load_own_env() -> None:
    env_path = _find_env_file()
    load_dotenv(dotenv_path=env_path, override=False)


def _resolve_mongo_credentials(alias: str, fields: Dict[str, str]) -> Tuple[str, str]:
    user = fields.get("USER")
    password = fields.get("PASSWORD")
    credentials_env = fields.get(_CREDENTIAL_ENV_FIELD)

    if credentials_env:
        user, password = _secret_loader.resolve_secret_reference(credentials_env.strip())

    missing = [name for name, value in (("USER", user), ("PASSWORD", password)) if not value]
    if missing:
        raise ValueError(
            f"Config Mongo incompleta para alias '{alias}'. Faltan credenciales: {missing}. "
            f"Define USER/PASSWORD o {_CREDENTIAL_ENV_FIELD}."
        )

    return str(user), str(password)


def load_app_config() -> AppConfig:
    return AppConfig(
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        output_dir=os.getenv("OUTPUT_DIR", "./output"),
        server_selection_timeout_ms=int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "20000")),
    )


def _build_ssh_params(alias: str, fields: Dict[str, str]) -> SSHTunnelParams:
    missing = _REQUIRED_SSH_FIELDS - set(fields.keys())
    if missing:
        raise ValueError(
            f"Config Mongo SSH incompleta para alias '{alias}'. Faltan: {sorted(missing)}"
        )
    return SSHTunnelParams(
        host=str(fields["SSH_HOST"]),
        port=int(fields.get("SSH_PORT", "22")),
        user=str(fields["SSH_USER"]),
        pkey_path=str(fields["SSH_KEY_PATH"]),
        local_port=int(fields["LOCAL_PORT"]),
        remote_host=str(fields["REMOTE_HOST"]),
        remote_port=int(fields["REMOTE_PORT"]),
    )


def _build_ssm_params(alias: str, fields: Dict[str, str]) -> SSMTunnelParams:
    missing = _REQUIRED_SSM_FIELDS - set(fields.keys())
    if missing:
        raise ValueError(
            f"Config Mongo SSM incompleta para alias '{alias}'. Faltan: {sorted(missing)}"
        )
    return SSMTunnelParams(
        aws_region=str(fields["AWS_REGION"]),
        target=str(fields["SSM_TARGET"]),
        local_port=int(fields["LOCAL_PORT"]),
        remote_host=str(fields["REMOTE_HOST"]),
        remote_port=int(fields["REMOTE_PORT"]),
        ssm_command=str(fields["SSM_COMMAND"]),
    )


def load_config() -> Tuple[AppConfig, Dict[str, MongoConfig]]:
    """
    Carga unicamente configuracion desde .env.mongo_extractor.
    No carga .env del proyecto host explicitamente.
    """
    _load_own_env()
    app = load_app_config()

    buckets: Dict[str, Dict[str, str]] = {}
    for key, value in os.environ.items():
        match = _MONGO_KEY_RE.match(key)
        if not match:
            continue
        alias = match.group("alias").lower()
        field = match.group("field")
        buckets.setdefault(alias, {})[field] = value

    if not buckets:
        raise ValueError("No se encontraron variables MONGO__<alias>__* en .env.mongo_extractor")

    profiles: Dict[str, MongoConfig] = {}
    for alias, fields in buckets.items():
        missing_common = _REQUIRED_COMMON_FIELDS - set(fields.keys())
        if missing_common:
            raise ValueError(
                f"Config Mongo incompleta para alias '{alias}'. Faltan: {sorted(missing_common)}"
            )

        tunnel = fields["TUNNEL"].lower().strip()
        if tunnel not in ("ssh", "ssm"):
            raise ValueError(
                f"TUNNEL invalido para alias '{alias}': '{tunnel}'. Valores aceptados: ssh|ssm"
            )

        user, password = _resolve_mongo_credentials(alias, fields)

        ssh_params = _build_ssh_params(alias, fields) if tunnel == "ssh" else None
        ssm_params = _build_ssm_params(alias, fields) if tunnel == "ssm" else None

        profiles[alias] = MongoConfig(
            tunnel=tunnel,  # type: ignore[arg-type]
            db=str(fields["DB"]),
            uri_template=str(fields["URI"]),
            user=user,
            password=password,
            warmup_s=float(fields.get("WARMUP_S", "1")),
            ssh=ssh_params,
            ssm=ssm_params,
        )

    return app, profiles
