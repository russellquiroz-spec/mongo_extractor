from __future__ import annotations

import logging
import os
import re
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

_DOCUMENTO_PORT_FORWARD = "AWS-StartPortForwardingSessionToRemoteHost"

#: `aws ssm start-session` abre su sesion y la anuncia en stdout con
#: "Starting session with SessionId: <id>". Ese es el unico lugar donde el id de la
#: sesion que de verdad reenvia queda disponible para poder cerrarla.
_SESSION_ID_RE = re.compile(r"SessionId:\s*(\S+)")

log = logging.getLogger(__name__)


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


def _texto_salida(salida: Optional[IO[bytes]]) -> Optional[str]:
    """Devuelve todo lo que el forwarder escribio, o None si no se pudo leer."""
    if salida is None:
        return None
    try:
        salida.flush()
        salida.seek(0)
        return salida.read().decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        return None


def _leer_salida(salida: Optional[IO[bytes]], limite: int = 2000) -> str:
    if salida is None:
        return "(sin salida capturada)"
    texto = _texto_salida(salida)
    if texto is None:
        return "(no se pudo leer la salida del forwarder)"
    if not texto:
        return "(el forwarder no escribio nada)"
    return texto[-limite:]


def _extraer_session_id(salida: Optional[IO[bytes]]) -> Optional[str]:
    texto = _texto_salida(salida)
    if not texto:
        return None
    match = _SESSION_ID_RE.search(texto)
    return match.group(1) if match else None


def _armar_comando(params: SSMTunnelParams) -> str:
    """
    Arma el `aws ssm start-session` desde el perfil.

    Antes el comando se leia crudo de SSM_COMMAND, asi que el destino real del tunel
    era el que iba dentro de ese string y REMOTE_HOST/REMOTE_PORT solo alimentaban una
    segunda sesion que no reenviaba nada. Los dos podian apuntar a clusters distintos
    sin que nada lo delatara. Armandolo aqui, la config es la unica fuente de verdad.
    """
    return (
        "aws ssm start-session"
        f' --region "{params.aws_region}"'
        f' --target "{params.target}"'
        f" --document-name {_DOCUMENTO_PORT_FORWARD}"
        f' --parameters host="{params.remote_host}"'
        f',portNumber="{params.remote_port}"'
        f',localPortNumber="{params.local_port}"'
    )


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
    - Lanza UN `aws ssm start-session` como subprocess, armado desde el perfil, que es
      el que hace el forwarding en localhost:LOCAL_PORT.
    - Espera a que el puerto quede escuchando, o falla con el detalle.
    - Al salir del contexto mata el arbol de procesos completo y termina en AWS la
      sesion que ese comando abrio.

    El warmup (espera adicional para que el tunel quede listo) NO se hace aqui; es
    responsabilidad del extractor, que conoce el WARMUP_S por perfil.

    Antes se abrian DOS sesiones por tunel: una con `boto3.start_session` y otra la del
    subprocess. Se midio que la de boto3 no reenvia nada —comentandola, el tunel sigue
    funcionando igual— y que la del subprocess nunca se cerraba: sobrevivia a que se
    matara el proceso local y quedaba Active en AWS unos 20 minutos mas, hasta que el
    idle timeout la barria. Ahora se abre una sola y se cierra explicitamente.

    Dos cosas que conviene saber de esta ruta:

    1. El SessionId de la sesion que reenvia solo esta disponible en la salida del
       comando, asi que se parsea de ahi. Si no se logra parsear, el tunel funciona
       igual pero el cierre queda a cargo del idle timeout de AWS: se avisa con WARNING.
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

    if params.ssm_command:
        log.warning(
            "SSM_COMMAND esta deprecado: se ejecuta tal cual y se ignoran AWS_REGION, "
            "SSM_TARGET, REMOTE_HOST, REMOTE_PORT y LOCAL_PORT para armar el comando, "
            "asi que el destino real del tunel es el que va dentro de SSM_COMMAND. "
            "Quitalo del perfil para que se arme desde la config."
        )
        comando = params.ssm_command
    else:
        comando = _armar_comando(params)

    proc: Optional[subprocess.Popen] = None
    session_id: Optional[str] = None
    salida: Optional[IO[bytes]] = None

    try:
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
            comando,
            shell=True,
            stdout=salida,
            stderr=subprocess.STDOUT,
            **extra,
        )

        _esperar_arranque(proc, params.local_port, salida)

        session_id = _extraer_session_id(salida)
        if session_id is None:
            log.warning(
                "No se pudo parsear el SessionId de la salida del forwarder. El tunel "
                "funciona, pero al cerrar no se podra terminar la sesion en AWS: "
                "quedara Active hasta que el idle timeout la barra."
            )

        yield SSMTunnelHandle(
            session_id=session_id, proc=proc, local_port=params.local_port
        )

    finally:
        _matar_arbol(proc)

        # Con el proceso ya muerto la salida esta completa: si el forwarder alcanzo a
        # crear la sesion pero murio antes de dejar el puerto escuchando, este es el
        # unico momento en que se puede rescatar su SessionId para cerrarla.
        if session_id is None:
            session_id = _extraer_session_id(salida)

        if session_id is not None:
            try:
                boto3.client("ssm", region_name=params.aws_region).terminate_session(
                    SessionId=session_id
                )
            except Exception as exc:  # noqa: BLE001 - el cierre nunca debe lanzar
                log.warning(
                    "No se pudo terminar la sesion de SSM %s: %s. Quedara Active hasta "
                    "que el idle timeout de AWS la barra.",
                    session_id,
                    exc,
                )

        if salida is not None:
            try:
                salida.close()
            except Exception:  # noqa: BLE001
                pass
