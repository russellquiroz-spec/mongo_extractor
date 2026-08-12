"""
Tests del tunel por SSM. No tocan AWS: el cliente de boto3 se sustituye.

Cubren los fallos que se detectaron en produccion:
  1. El arbol de procesos del forwarder no se mataba, dejando huerfanos con el puerto
     local tomado.
  2. Al estar el puerto tomado, el tunel nuevo no hacia bind y el cliente terminaba
     hablando con el tunel viejo sin ningun error.
  3. Se abrian DOS sesiones de SSM por tunel y solo se cerraba una. La de
     `boto3.start_session` no reenviaba nada y era la unica que se terminaba; la del
     subprocess, que es la que de verdad reenvia, quedaba Active en AWS hasta que el
     idle timeout la barria (~20 min medidos).
"""

from __future__ import annotations

import logging
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
        return {"SessionId": "sesion-de-boto3"}

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


def _params(local_port: int, ssm_command: str | None = None) -> SSMTunnelParams:
    return SSMTunnelParams(
        aws_region="us-east-2",
        target="i-000000000000",
        local_port=local_port,
        remote_host="cluster.example.internal",
        remote_port=27017,
        ssm_command=ssm_command,
    )


def _anuncio(session_id: str | None) -> str:
    """
    Imita la linea con la que `aws ssm start-session` anuncia su sesion.

    Es el unico lugar donde el SessionId de la sesion que de verdad reenvia queda
    disponible, asi que el forwarder falso tiene que imprimirla para que el test
    ejercite el parseo real.
    """
    if session_id is None:
        return ""
    return f"print('Starting session with SessionId: {session_id}',flush=True);"


def _comando_que_ocupa_el_puerto(
    port: int, segundos: int = 60, session_id: str | None = "sesion-de-prueba"
) -> str:
    """
    Comando que se comporta como el forwarder real: anuncia su SessionId, deja un hijo
    ocupando el puerto local y sobrevive a que se mate el shell intermedio.

    Con shell=True, Popen devuelve el shell; el proceso que escucha es su hijo. Es
    exactamente la topologia que dejaba huerfanos a aws.exe y session-manager-plugin.exe.
    """
    guion = (
        "import socket,time;"
        + _anuncio(session_id)
        + "s=socket.socket();"
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


def _esperar_puerto_libre(port: int) -> None:
    for _ in range(20):
        if not _escuchando(port):
            return
        time.sleep(0.25)


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
    _esperar_puerto_libre(port)
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
        _esperar_puerto_libre(port)
        assert not _escuchando(port)


def test_forwarder_que_muere_al_arrancar_falla_con_su_salida(cliente_falso, monkeypatch):
    """
    Un comando que termina de inmediato debe fallar explicando por que, no entregar un
    tunel muerto para que el cliente reviente despues con un error de conexion.

    Ademas es el caso que justifica reintentar el parseo en el `finally`: la sesion se
    creo pero el puerto nunca abrio, y hay que cerrarla igual.
    """
    monkeypatch.setattr(ssm_module, "ARRANQUE_TIMEOUT_S", 10.0)
    port = _puerto_libre()
    comando = (
        f'"{sys.executable}" -c "'
        + _anuncio("sesion-de-prueba")
        + "import sys;sys.stderr.write('fallo de prueba');sys.exit(3)"
        '"'
    )

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
    _esperar_puerto_libre(port)
    assert not _escuchando(port)


@pytest.mark.skipif(os.name != "nt", reason="taskkill /T es de Windows")
def test_matar_arbol_es_tolerante_a_un_proceso_ya_muerto():
    """El cierre nunca debe lanzar, aunque el proceso ya no exista."""
    import subprocess

    proc = subprocess.Popen(f'"{sys.executable}" -c "pass"', shell=True)
    proc.wait(timeout=30)
    ssm_module._matar_arbol(proc)
    ssm_module._matar_arbol(None)


# --- Una sola sesion por tunel: comando armado desde la config y cierre explicito ---


def test_no_se_abre_ninguna_sesion_con_boto3(cliente_falso):
    """
    La sesion de boto3 se elimino: se midio que no reenvia nada (comentandola el tunel
    sigue funcionando) y era la unica que se cerraba, mientras la que si trabaja se
    quedaba Active. boto3 ahora solo se usa para terminar.
    """
    port = _puerto_libre()
    with open_ssm_tunnel(_params(port, _comando_que_ocupa_el_puerto(port))):
        pass

    assert cliente_falso.iniciadas == 0
    assert cliente_falso.terminadas == ["sesion-de-prueba"]
    _esperar_puerto_libre(port)


def test_arma_el_comando_desde_la_config():
    """
    El comando sale del perfil, no de un string crudo. Asi desaparece la posibilidad de
    que REMOTE_HOST/REMOTE_PORT y el destino real del tunel apunten a clusters distintos.
    """
    comando = ssm_module._armar_comando(_params(27017))

    assert comando.startswith("aws ssm start-session")
    assert '--region "us-east-2"' in comando
    assert '--target "i-000000000000"' in comando
    assert "--document-name AWS-StartPortForwardingSessionToRemoteHost" in comando
    assert '--parameters host="cluster.example.internal"' in comando
    assert 'portNumber="27017"' in comando
    assert 'localPortNumber="27017"' in comando


def test_sin_ssm_command_ejecuta_el_comando_armado(cliente_falso, monkeypatch):
    """Si el perfil no trae SSM_COMMAND, se ejecuta lo que arma _armar_comando."""
    port = _puerto_libre()
    recibidos: list[SSMTunnelParams] = []

    def _fingir_armado(params: SSMTunnelParams) -> str:
        recibidos.append(params)
        return _comando_que_ocupa_el_puerto(params.local_port)

    monkeypatch.setattr(ssm_module, "_armar_comando", _fingir_armado)

    params = _params(port)
    assert params.ssm_command is None
    with open_ssm_tunnel(params) as handle:
        assert _escuchando(port)
        assert handle.session_id == "sesion-de-prueba"

    assert recibidos == [params], "no se armo el comando desde el perfil"
    assert cliente_falso.terminadas == ["sesion-de-prueba"]
    _esperar_puerto_libre(port)


def test_ssm_command_sigue_funcionando_con_warning(cliente_falso, monkeypatch, caplog):
    """
    Compatibilidad con los .env existentes: si el perfil trae SSM_COMMAND se usa tal
    cual, sin armar nada, y se avisa de que esta deprecado.
    """
    def _no_deberia_armar(_params):
        pytest.fail("con SSM_COMMAND presente no se debe armar el comando")

    monkeypatch.setattr(ssm_module, "_armar_comando", _no_deberia_armar)

    port = _puerto_libre()
    with caplog.at_level(logging.WARNING, logger=ssm_module.log.name):
        with open_ssm_tunnel(_params(port, _comando_que_ocupa_el_puerto(port))):
            assert _escuchando(port)

    mensajes = [r.getMessage() for r in caplog.records]
    assert any("SSM_COMMAND esta deprecado" in m for m in mensajes), (
        f"no se aviso del deprecado: {mensajes}"
    )
    assert cliente_falso.terminadas == ["sesion-de-prueba"]
    _esperar_puerto_libre(port)


def test_sin_session_id_parseable_el_tunel_no_falla(cliente_falso, caplog):
    """
    Perder el cierre explicito es peor que hoy, pero no justifica tumbar la operacion:
    se avisa con WARNING y el tunel sigue entregandose.
    """
    port = _puerto_libre()
    comando = _comando_que_ocupa_el_puerto(port, session_id=None)

    with caplog.at_level(logging.WARNING, logger=ssm_module.log.name):
        with open_ssm_tunnel(_params(port, comando)) as handle:
            assert _escuchando(port), "el tunel debe funcionar igual"
            assert handle.session_id is None

    mensajes = [r.getMessage() for r in caplog.records]
    assert cliente_falso.terminadas == [], "no habia SessionId que terminar"
    assert any("No se pudo parsear el SessionId" in m for m in mensajes), (
        f"no se aviso de que no se pudo parsear: {mensajes}"
    )
    # Y el arbol se mato igual, aunque no se pudiera cerrar la sesion en AWS.
    _esperar_puerto_libre(port)
    assert not _escuchando(port)


def test_extraer_session_id_ignora_la_linea_del_puerto(tmp_path):
    """
    El forwarder imprime dos lineas con el id; solo la primera trae 'SessionId:'. El
    parseo tiene que quedarse con esa y no con la del puerto.
    """
    ruta = tmp_path / "salida.txt"
    ruta.write_bytes(
        b"Starting session with SessionId: usuario-abc123\n"
        b"Port 27017 opened for sessionId usuario-abc123.\n"
        b"Waiting for connections...\n"
    )
    with ruta.open("rb") as fh:
        assert ssm_module._extraer_session_id(fh) == "usuario-abc123"

    vacio = tmp_path / "vacio.txt"
    vacio.write_bytes(b"")
    with vacio.open("rb") as fh:
        assert ssm_module._extraer_session_id(fh) is None
    assert ssm_module._extraer_session_id(None) is None
