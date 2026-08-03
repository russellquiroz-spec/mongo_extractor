from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from mongo_extractor.tunnels.ssh import open_ssh_tunnel
from mongo_extractor.tunnels.ssm import SSMTunnelHandle, open_ssm_tunnel
from mongo_extractor.types import MongoConfig


@contextmanager
def open_tunnel(profile: MongoConfig) -> Iterator[int]:
    """
    Abre el tunel correcto segun profile.tunnel ('ssh' o 'ssm') y
    yieldea el puerto local donde escucha el forwarder.

    El llamador es responsable de aplicar profile.warmup_s antes de
    intentar conectar al servicio.
    """
    if profile.tunnel == "ssh":
        if profile.ssh is None:
            raise ValueError("Profile marcado como ssh pero sin parametros SSH.")
        with open_ssh_tunnel(profile.ssh) as forwarder:
            yield int(forwarder.local_bind_port)
        return

    if profile.tunnel == "ssm":
        if profile.ssm is None:
            raise ValueError("Profile marcado como ssm pero sin parametros SSM.")
        with open_ssm_tunnel(profile.ssm) as handle:
            yield handle.local_port
        return

    raise ValueError(f"Tunnel mode no soportado: {profile.tunnel}")
