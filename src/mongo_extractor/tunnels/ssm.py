from __future__ import annotations

import os
import signal
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import IO, Any, Dict, Iterator, Optional

import boto3

from mongo_extractor.types import SSMTunnelParams

#: Cuanto se espera a que el forwarder deje el puerto local escuchando antes de
#: declarar que no arranco.
ARRANQUE_TIMEOUT_S = 30.0

_LOCALHOST = "127.0.0.1"


@dataclass
class SSMTunnelHandle:
    session_id: Optional[str]
    proc: Optional[subprocess.Popen]
    local_port: int


def _puerto_ocupado(port: int, host: str = _LOCALHOST) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        return True
    finally:
        probe.close()
    return False


def _puerto_escuchando(port: int, host: str = _LOCALHOST, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _leer_salida(salida: Optional[IO[bytes]], limite: int = 2000) -> str:
    if salida is None:
        return "(sin salida capturada)"
    try:
        salida.flush()
        salida.seek(0)
        texto = salida.read().decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        return "(no se pudo leer la salida del forwarder)"
    if not texto:
        return "(el forwarder no escribio nada)"
    return texto[-limite:]


def _matar_arbol(proc: Optional[subprocess.Popen]) -> None:
    """
    Mata el proceso Y todos sus hijos.

    `Popen(..., shell=True)` devuelve el shell intermedio (`cmd.exe` en Windows), asi
    que `terminate()` mata solo ese shell y deja vivos a `aws.exe` y
    `session-manager-plugin.exe`. Esos huerfanos se quedan con el puerto local tomado,
    y como el puerto es fijo, el siguiente tunel no puede hacer bind y el cliente
    termina hablando con el tunel viejo —posiblemente contra otro cluster— sin ningun
    error. Por eso hay que matar el arbol completo.
    """
    if proc is None or proc.poll() is not None:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
        except Exception:  # noqa: BLE001 - el cierre nunca debe lanzar
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass

    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _esperar_arranque(
    proc: subprocess.Popen, port: int, salida: Optional[IO[bytes]]
) -> None:
    """
    Espera a que el puerto local quede escuchando, o falla explicando por que no.

    Sin esto, un forwarder que muere al arrancar (comando mal armado, sesion SSM
    rechazada, plugin sin instalar) pasaba desapercibido: el contexto entregaba el
    puerto igual y el cliente fallaba despues con un error de conexion que no apunta
    a la causa.
    """
    limite = time.monotonic() + ARRANQUE_TIMEOUT_S
    while time.monotonic() < limite:
        if proc.poll() is not None:
            raise RuntimeError(
                f"El forwarder de SSM termino con codigo {proc.returncode} antes de dejar "
                f"el puerto {port} escuchando.\nSalida del forwarder:\n{_leer_salida(salida)}"
            )
        if _puerto_escuchando(port):
            return
        time.sleep(0.25)

    raise RuntimeError(
        f"El forwarder de SSM no dejo el puerto {port} escuchando en "
        f"{ARRANQUE_TIMEOUT_S:g}s.\nSalida del forwarder:\n{_leer_salida(salida)}"
    )


@contextmanager
def open_ssm_tunnel(params: SSMTunnelParams) -> Iterator[SSMTunnelHandle]:
    """
    Inicia un port-forward via AWS SSM y garantiza que quede cerrado al salir.

    - Verifica que el puerto local este libre ANTES de empezar.
    - Llama boto3 ssm.start_session para registrar la sesion remota.
    - Lanza el comando de SSM_COMMAND como subprocess, que es el que de verdad hace
      el forwarding en localhost:LOCAL_PORT.
    - Espera a que el puerto quede escuchando, o falla con el detalle.
    - Al salir del contexto mata el arbol de procesos completo y termina la sesion.

    El warmup (espera adicional para que el tunel quede listo) NO se hace aqui; es
    responsabilidad del extractor, que conoce el WARMUP_S por perfil.

    Dos cosas que conviene saber de esta ruta:

    1. Se abren DOS sesiones de SSM por tunel. La de `boto3.start_session` no reenvia
       nada por si sola; el forwarding lo hace el subprocess, que abre su propia
       sesion. La consecuencia practica es que **el destino efectivo es el que va
       dentro de SSM_COMMAND**, no el de REMOTE_HOST/REMOTE_PORT del perfil: esos solo
       alimentan la sesion de boto3. Si los dos no coinciden, el que manda es
       SSM_COMMAND.
    2. LOCAL_PORT es fijo. Si el proceso muere de forma que no corra el `finally`
       (un kill -9, por ejemplo), el forwarder puede quedar huerfano con el puerto
       tomado; la verificacion de arriba lo detecta en la siguiente corrida en vez de
       dejar que el cliente hable con el tunel viejo.
    """
    if _puerto_ocupado(params.local_port):
        raise RuntimeError(
            f"El puerto local {params.local_port} ya esta ocupado, asi que este tunel no "
            "podria hacer bind y el cliente acabaria conectandose a lo que ya escucha ahi "
            "—tipicamente un forwarder huerfano de una corrida anterior, que puede apuntar "
            "a otro cluster. Revisa quien lo tiene y liberalo:\n"
            f"  Get-NetTCPConnection -LocalPort {params.local_port} -State Listen | "
            "Select-Object OwningProcess\n"
            "  Get-Process aws, session-manager-plugin | Stop-Process -Force"
        )

    ssm_client = boto3.client("ssm", region_name=params.aws_region)
    proc: Optional[subprocess.Popen] = None
    session_id: Optional[str] = None
    salida: Optional[IO[bytes]] = None

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

        # Archivo temporal en vez de subprocess.PIPE: nadie lee esas tuberias, y cuando
        # el buffer del pipe se llena (64 KB en Windows) el plugin se bloquea
        # escribiendo y el tunel deja de reenviar sin caerse, que es peor de
        # diagnosticar que una caida. Un archivo no se llena, y ademas deja la salida
        # disponible para el mensaje de error si el arranque falla.
        salida = tempfile.TemporaryFile(mode="w+b")

        extra: Dict[str, Any] = {}
        if os.name != "nt":
            # Necesario para poder matar el grupo completo con killpg.
            extra["start_new_session"] = True

        proc = subprocess.Popen(
            params.ssm_command,
            shell=True,
            stdout=salida,
            stderr=subprocess.STDOUT,
            **extra,
        )

        _esperar_arranque(proc, params.local_port, salida)

        yield SSMTunnelHandle(
            session_id=session_id, proc=proc, local_port=params.local_port
        )

    finally:
        _matar_arbol(proc)

        if salida is not None:
            try:
                salida.close()
            except Exception:  # noqa: BLE001
                pass

        try:
            if session_id is not None:
                ssm_client.terminate_session(SessionId=session_id)
        except Exception:  # noqa: BLE001
            pass
