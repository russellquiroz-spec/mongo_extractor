from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

TunnelMode = Literal["ssh", "ssm"]


@dataclass(frozen=True)
class SSHTunnelParams:
    host: str
    port: int
    user: str
    pkey_path: str
    local_port: int
    remote_host: str
    remote_port: int


@dataclass(frozen=True)
class SSMTunnelParams:
    aws_region: str
    target: str
    local_port: int
    remote_host: str
    remote_port: int
    ssm_command: str


@dataclass(frozen=True)
class MongoConfig:
    tunnel: TunnelMode
    db: str
    uri_template: str
    user: str
    password: str
    warmup_s: float
    ssh: Optional[SSHTunnelParams] = None
    ssm: Optional[SSMTunnelParams] = None


@dataclass(frozen=True)
class AppConfig:
    log_level: str = "INFO"
    output_dir: str = "./output"
    server_selection_timeout_ms: int = 20000
