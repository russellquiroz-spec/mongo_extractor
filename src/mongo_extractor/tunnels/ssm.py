from __future__ import annotations

import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

import boto3

from mongo_extractor.types import SSMTunnelParams


@dataclass
class SSMTunnelHandle:
    session_id: Optional[str]
    proc: Optional[subprocess.Popen]
    local_port: int


@contextmanager
def open_ssm_tunnel(params: SSMTunnelParams) -> Iterator[SSMTunnelHandle]:
    """
    Inicia un port-forward via AWS SSM:
    - Llama boto3 ssm.start_session para registrar la sesion remota.
    - Lanza el comando 'aws ssm start-session ...' como subprocess para mantener
      el forwarder activo en localhost:LOCAL_PORT.
    - Al salir del contexto, termina el subprocess y la sesion SSM.

    El warmup (espera para que el tunel quede listo) NO se hace aqui;
    es responsabilidad del extractor que conoce el WARMUP_S por perfil.
    """
    ssm_client = boto3.client("ssm", region_name=params.aws_region)
    proc: Optional[subprocess.Popen] = None
    session_id: Optional[str] = None

    try:
        response = ssm_client.start_session(
            Target=params.target,
            DocumentName="AWS-StartPortForwardingSessionToRemoteHost",
            Parameters={
                "host": [params.remote_host],
                "portNumber": [str(params.remote_port)],
                "localPortNumber": [str(params.local_port)],
            },
        )
        session_id = response["SessionId"]

        proc = subprocess.Popen(
            params.ssm_command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        yield SSMTunnelHandle(session_id=session_id, proc=proc, local_port=params.local_port)

    finally:
        try:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass

        try:
            if session_id is not None:
                ssm_client.terminate_session(SessionId=session_id)
        except Exception:
            pass
