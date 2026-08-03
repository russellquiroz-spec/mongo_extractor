from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sshtunnel import SSHTunnelForwarder

from mongo_extractor.types import SSHTunnelParams


@contextmanager
def open_ssh_tunnel(params: SSHTunnelParams) -> Iterator[SSHTunnelForwarder]:
    """
    Abre un tunel SSH local:remoto y entrega el forwarder activo.
    """
    with SSHTunnelForwarder(
        (params.host, params.port),
        ssh_username=params.user,
        ssh_pkey=params.pkey_path,
        remote_bind_address=(params.remote_host, params.remote_port),
        local_bind_address=("127.0.0.1", params.local_port),
    ) as tunnel:
        yield tunnel
