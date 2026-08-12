"""
Tests del tunel por SSM. No tocan AWS: el cliente de boto3 se sustituye.

Cubren los dos fallos que se detectaron en produccion:
  1. El arbol de procesos del forwarder no se mataba, dejando huerfanos con el puerto
     local tomado.
  2. Al estar el puerto tomado, el tunel nuevo no hacia bind y el cliente terminaba
     hablando con el tunel viejo sin ningun error.
"""

from __future__ import annotations

import os
import socket
import sys
import time

import pytest

from mongo_extractor.tunnels import ssm as ssm_module
from mongo_extractor.tunnels.ssm import SSMTunnelHandle, open_ssm_tunnel
from mongo_extractor.types import SSMTunnelParams


class _ClienteFalso:
    """Sustituto de boto3: registra las llamadas y no sale a la red."""

    def __init__(self) -> None:
        self.iniciadas = 0
        self.terminadas: list[str] = []

    def start_session(self, **_kwargs) -> dict:
        self.iniciadas += 1
        return {"SessionId": "sesion-de-prueba"}

    def terminate_session(self, SessionId: str) -> dict:  # noqa: N803 - firma de boto3
        self.terminadas.append(SessionId)
        return {}


@pytest.fixture
def cliente_falso(monkeypatch) -> _ClienteFalso:
    cliente = _ClienteFalso()
    monkeypatch.setattr(ssm_module.boto3, "client", lambda *a, **k: cliente)
    return cliente


def _puerto_libre() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _params(local_port: int, ssm_command: str) -> SSMTunnelParams:
    return SSMTunnelParams(
        aws_region="us-east-2",
        target="i-000000000000",
        local_port=local_port,
        remote_host="cluster.example.internal",
        remote_port=27017,
        ssm_command=ssm_command,
    )


def _comando_que_ocupa_el_puerto(port: int, segundos: int = 60) -> str:
    """
    Comando que se comporta como el forwarder real: un hijo que ocupa el puerto local
    y que sobrevive a que se mate el shell intermedio.

    Con shell=True, Popen devuelve el shell; el proceso que escucha es su hijo. Es
    exactamente la topologia que dejaba huerfanos a aws.exe y session-manager-plugin.exe.
    """
    guion = (
        "import socket,time;"
        "s=socket.socket();"
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        f"s.bind(('127.0.0.1',{port}));"
        "s.listen(8);"
        f"time.sleep({segundos})"
    )
    return f'"{sys.executable}" -c "{guion}"'


def _escuchando(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def test_puerto_ocupado_falla_antes_de_tocar_aws(monkeypatch):
    """
    Si el puerto local ya esta tomado, se falla de inmediato.

    Antes no se verificaba: el forwarder nuevo no lograba hacer bind y MongoClient se
    conectaba a lo que ya escuchaba ahi, tipicamente un tunel huerfano contra otro
    cluster. Un fallo silencioso que devuelve datos equivocados.
    """
    def _explota(*_a, **_k):
        pytest.fail("no debe tocar AWS si el puerto esta ocupado")

    monkeypatch.setattr(ssm_module.boto3, "client", _explota)

    ocupado = socket.socket()
    ocupado.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ocupado.bind(("127.0.0.1", 0))
    ocupado.listen(1)
    port = ocupado.getsockname()[1]
    try:
        with pytest.raises(RuntimeError) as excinfo:
            with open_ssm_tunnel(_params(port, "no-deberia-ejecutarse")):
                pass
        mensaje = str(excinfo.value)
        assert str(port) in mensaje
        assert "ocupado" in mensaje
        # El mensaje tiene que decir como salir del problema.
        assert "Stop-Process" in mensaje
    finally:
        ocupado.close()


def test_mata_el_arbol_y_libera_el_puerto(cliente_falso):
    """
    Al salir del contexto no debe quedar nada escuchando en el puerto local.

    Es el bug original: `terminate()` sobre un Popen con shell=True mata solo el shell,
    y el proceso que tiene el puerto sobrevive.
    """
    port = _puerto_libre()
    comando = _comando_que_ocupa_el_puerto(port)

    with open_ssm_tunnel(_params(port, comando)) as handle:
        assert isinstance(handle, SSMTunnelHandle)
        assert handle.local_port == port
        assert handle.session_id == "sesion-de-prueba"
        # El contexto no entrega hasta que el puerto esta escuchando de verdad.
        assert _escuchando(port)

    # Y al salir, el arbol completo murio.
    for _ in range(20):
        if not _escuchando(port):
            break
        time.sleep(0.25)
    assert not _escuchando(port), (
        f"quedo algo escuchando en {port}: el arbol de procesos no se mato"
    )
    assert cliente_falso.terminadas == ["sesion-de-prueba"]


def test_dos_tuneles_seguidos_en_el_mismo_puerto(cliente_falso):
    """
    Consecuencia directa del arreglo: como el puerto queda libre, el siguiente tunel
    sobre el mismo LOCAL_PORT funciona. Antes fallaba el bind en silencio.
    """
    port = _puerto_libre()
    for _ in range(2):
        with open_ssm_tunnel(_params(port, _comando_que_ocupa_el_puerto(port))):
            assert _escuchando(port)
        for _ in range(20):
            if not _escuchando(port):
                break
            time.sleep(0.25)
        assert not _escuchando(port)


def test_forwarder_que_muere_al_arrancar_falla_con_su_salida(cliente_falso, monkeypatch):
    """
    Un comando que termina de inmediato debe fallar explicando por que, no entregar un
    tunel muerto para que el cliente reviente despues con un error de conexion.
    """
    monkeypatch.setattr(ssm_module, "ARRANQUE_TIMEOUT_S", 10.0)
    port = _puerto_libre()
    comando = f'"{sys.executable}" -c "import sys; sys.stderr.write(\'fallo de prueba\'); sys.exit(3)"'

    with pytest.raises(RuntimeError) as excinfo:
        with open_ssm_tunnel(_params(port, comando)):
            pytest.fail("no deberia haber entregado el tunel")

    mensaje = str(excinfo.value)
    assert "termino con codigo" in mensaje
    # La salida del forwarder viaja en el error: es el detalle que permite diagnosticar.
    assert "fallo de prueba" in mensaje
    # Y la sesion de SSM se cerro igual.
    assert cliente_falso.terminadas == ["sesion-de-prueba"]


def test_forwarder_que_nunca_levanta_el_puerto(cliente_falso, monkeypatch):
    """Si el proceso vive pero nunca deja el puerto escuchando, se falla por timeout."""
    monkeypatch.setattr(ssm_module, "ARRANQUE_TIMEOUT_S", 2.0)
    port = _puerto_libre()
    comando = f'"{sys.executable}" -c "import time; time.sleep(30)"'

    inicio = time.monotonic()
    with pytest.raises(RuntimeError, match="no dejo el puerto"):
        with open_ssm_tunnel(_params(port, comando)):
            pytest.fail("no deberia haber entregado el tunel")
    transcurrido = time.monotonic() - inicio

    assert transcurrido < 20, "el timeout no se respeto"
    # Y no quedo el proceso colgado.
    assert not _escuchando(port)


def test_la_sesion_se_termina_aunque_el_cuerpo_lance(cliente_falso):
    port = _puerto_libre()
    with pytest.raises(ValueError, match="explota"):
        with open_ssm_tunnel(_params(port, _comando_que_ocupa_el_puerto(port))):
            raise ValueError("explota a proposito")

    assert cliente_falso.terminadas == ["sesion-de-prueba"]
    for _ in range(20):
        if not _escuchando(port):
            break
        time.sleep(0.25)
    assert not _escuchando(port)


@pytest.mark.skipif(os.name != "nt", reason="taskkill /T es de Windows")
def test_matar_arbol_es_tolerante_a_un_proceso_ya_muerto():
    """El cierre nunca debe lanzar, aunque el proceso ya no exista."""
    import subprocess

    proc = subprocess.Popen(f'"{sys.executable}" -c "pass"', shell=True)
    proc.wait(timeout=30)
    ssm_module._matar_arbol(proc)
    ssm_module._matar_arbol(None)
