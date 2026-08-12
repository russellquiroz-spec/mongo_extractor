mongo_extractor

Libreria interna y CLI opcional para extraer datos desde MongoDB / DocumentDB.
Soporta multiples perfiles, cada uno con su propio mecanismo de tunneling
(SSH directo o AWS SSM port-forward), y carga un env propio
(`.env.mongo_extractor`) sin depender del `.env` del proyecto host.

--------------------------------------------------------------------------------
QUE HACE
--------------------------------------------------------------------------------

- Abre el tunel correcto segun el perfil (`TUNNEL=ssh|ssm`).
- Conecta a Mongo/DocDB via `pymongo`.
- Ejecuta un pipeline de agregacion sobre la coleccion indicada y devuelve un `pandas.DataFrame`.
- Acepta el pipeline como lista de etapas, como texto JSON o como archivo `.json`
  (ruta absoluta o relativa).
- Opcionalmente guarda CSV y/o Parquet sin dejar de devolver el DataFrame.
- Ejecuta pipelines guardados como archivo `.json`, con `$limit` automatico para pruebas
  rapidas y reintentos ante fallas de conexion/tunel.
- Permite definir varios perfiles por alias (ej. `bnpl`, `tx`).
- Emite eventos de estado estructurados para que el proyecto host los imprima, registre o muestre en UI.

--------------------------------------------------------------------------------
PRINCIPIOS DE DISENO
--------------------------------------------------------------------------------

- Library-first: API limpia para ser llamada desde otros proyectos.
- Env aislado: carga solo `.env.mongo_extractor`.
- Credenciales fuera del repo: el env del extractor guarda configuracion no sensible y apunta a secretos externos.
- Multiples perfiles: seleccion por alias.
- Estado sin acoplamiento: la libreria no configura logging global.
- Fail-fast: errores explicitos y tempranos.
- Windows-friendly: normaliza aliases a lowercase y puede leer variables persistidas en registro.

--------------------------------------------------------------------------------
INSTALACION
--------------------------------------------------------------------------------

Editable install recomendado:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .
```

Con dependencias de desarrollo:

```powershell
pip install -e ".[dev]"
```

Tambien puedes usar el instalador local:

```powershell
python install.py
```

--------------------------------------------------------------------------------
CONFIGURACION: .env.mongo_extractor
--------------------------------------------------------------------------------

El extractor carga configuracion solo desde su env propio, en este orden:

1. `MONGO_EXTRACTOR_ENV_FILE` si esta definida.
2. Busqueda hacia arriba desde el package instalado hasta encontrar `.env.mongo_extractor`.

Importante: nunca carga automaticamente el `.env` del proyecto host.

### App opcional

```env
LOG_LEVEL=INFO
OUTPUT_DIR=./output
MONGO_SERVER_SELECTION_TIMEOUT_MS=20000
```

### Mongo por perfil

Variables comunes a cualquier perfil:

```env
MONGO__<ALIAS>__TUNNEL=ssh|ssm
MONGO__<ALIAS>__DB=<database>
MONGO__<ALIAS>__URI=mongodb://{user}:{password}@localhost:<LOCAL_PORT>/?...
MONGO__<ALIAS>__CREDENTIALS_ENV=<NOMBRE_DE_VARIABLE_DE_SISTEMA>
MONGO__<ALIAS>__WARMUP_S=1     # opcional, default 1 segundo
```

El `URI` debe contener los placeholders `{user}` y `{password}`. En runtime
se sustituyen con las credenciales resueltas vía `CREDENTIALS_ENV` (con
URL-encoding aplicado).

### Perfil con TUNNEL=ssh

```env
MONGO__tx__TUNNEL=ssh
MONGO__tx__DB=transactions
MONGO__tx__URI=mongodb://{user}:{password}@localhost:27018/?authSource=admin&directConnection=true&ssl=false
MONGO__tx__CREDENTIALS_ENV=RIM_FINTECH_BNPL_KEY
MONGO__tx__SSH_HOST=jump-host-server-rds.rabbitmx.com
MONGO__tx__SSH_PORT=22
MONGO__tx__SSH_USER=ec2-user
MONGO__tx__SSH_KEY_PATH=C:/.../jump-host-server-rds-key.pem
MONGO__tx__LOCAL_PORT=27018
MONGO__tx__REMOTE_HOST=tf-rabbit-default-docdb-cluster.cluster-xxx.us-east-2.docdb.amazonaws.com
MONGO__tx__REMOTE_PORT=27017
```

### Perfil con TUNNEL=ssm

```env
MONGO__bnpl__TUNNEL=ssm
MONGO__bnpl__DB=BNPL
MONGO__bnpl__URI=mongodb://{user}:{password}@localhost:27017/?authSource=admin&tls=true&tlsCAFile=C:/.../global-bundle.pem&...
MONGO__bnpl__CREDENTIALS_ENV=RIM_FINTECH_BNPL_KEY
MONGO__bnpl__WARMUP_S=10
MONGO__bnpl__AWS_REGION=us-east-2
MONGO__bnpl__SSM_TARGET=i-0d9002794c9ad3b62
MONGO__bnpl__LOCAL_PORT=27017
MONGO__bnpl__REMOTE_HOST=tf-rabbit-default-docdb-cluster.cluster-xxx.us-east-2.docdb.amazonaws.com
MONGO__bnpl__REMOTE_PORT=27017
```

El comando `aws ssm start-session` se arma desde estos campos, asi que no hay nada mas
que definir.

`SSM_COMMAND` esta **deprecado** y ya no es obligatorio. Si un perfil todavia lo trae, se
ejecuta tal cual y se ignoran `AWS_REGION`, `SSM_TARGET`, `REMOTE_HOST`, `REMOTE_PORT` y
`LOCAL_PORT` para armar el comando —con un WARNING al abrir el tunel—. Quitalo para que la
config sea la unica fuente de verdad del destino.

### Resolucion de credenciales

`CREDENTIALS_ENV` debe resolver a credenciales con `user` y `password`.

Formatos soportados para la variable de sistema:

```text
{"user":"db_user","password":"db_password"}
USER=db_user;PASSWORD=db_password
db_user:db_password
```

El extractor primero busca en KeyringManager
(`%APPDATA%\KeyringManager\credentials.json` con entrada `env_var=<CREDENTIALS_ENV>`),
luego en variables de sistema, y como fallback en el registro de Windows.

--------------------------------------------------------------------------------
USO COMO LIBRERIA
--------------------------------------------------------------------------------

Listar perfiles:

```python
from mongo_extractor import list_profiles

print(list_profiles())
# ['bnpl', 'tx']
```

Ejecutar agregacion:

```python
from mongo_extractor import extract_aggregate

pipeline = [
    {"$match": {"status": "active"}},
    {"$limit": 100},
]

df = extract_aggregate(
    profile="bnpl",
    collection="users",
    pipeline=pipeline,
)
print(df.head())
```

Guardar resultados y devolver DataFrame:

```python
df = extract_aggregate(
    "tx",
    "transactions",
    [{"$match": {"date": {"$gte": "2025-01-01"}}}, {"$limit": 1000}],
    save_dir=r"C:\Users\TuUsuario\Documents\salidas_mongo",
    base_name="tx_recientes",
    save_csv=True,
    save_parquet=True,
)
```

Si `save_dir` es `None` no se guarda nada. Si `base_name` es `None` se genera
`<profile>_<db>_<collection>_<YYYYmmdd_HHMMSS>`.

Parametros de `extract_aggregate`:

| Parametro        | Default    | Descripcion                                                            |
| ---------------- | ---------- | ---------------------------------------------------------------------- |
| `profile`        | (requerido)| Alias del perfil; se normaliza a lowercase.                            |
| `collection`     | `None`     | Coleccion. Opcional si el JSON trae `collection`.                      |
| `pipeline`       | `None`     | Lista de etapas o el mismo JSON como texto.                            |
| `pipeline_file`  | `None`     | Ruta a un `.json` (absoluta o relativa). Se ignora si se pasa `pipeline`. |
| `on_event`       | `None`     | Callback de eventos de estado.                                        |
| `save_dir`       | `None`     | Carpeta destino; si es `None` no guarda nada.                          |
| `base_name`      | `None`     | Nombre base sin extension; si es `None` se autogenera.                 |
| `save_csv`       | `False`    | Guarda CSV en `save_dir`.                                              |
| `save_parquet`   | `False`    | Guarda Parquet en `save_dir`.                                          |
| `csv_index`      | `False`    | Escribe el indice en el CSV.                                           |
| `csv_encoding`   | `utf-8`    | Encoding del CSV.                                                      |
| `parquet_index`  | `False`    | Escribe el indice en el Parquet.                                       |
| `allow_disk_use` | `True`     | `allowDiskUse` del `aggregate`.                                        |

### extract_aggregate: pipeline como lista, texto o archivo

`pipeline` acepta la lista de etapas o el mismo JSON en texto:

```python
df = extract_aggregate(
    profile="bnpl",
    collection="users",
    pipeline='[{"$match": {"status": "active"}}, {"$limit": 100}]',
)
```

En vez de `pipeline`, se puede pasar `pipeline_file` con la ruta de un `.json`.
Acepta rutas absolutas y relativas (las relativas se resuelven contra el directorio
de trabajo actual) y expande `~`:

```python
from mongo_extractor import extract_aggregate

# Relativa al cwd
df = extract_aggregate(
    profile="bnpl",
    collection="users",
    pipeline_file="queries/mongo/usuarios_activos.json",
)

# Absoluta
df = extract_aggregate(
    profile="bnpl",
    collection="users",
    pipeline_file=r"C:\Users\TuUsuario\Documents\queries\usuarios_activos.json",
)
```

Texto y archivo aceptan los mismos tres formatos: array de etapas, objeto
`{"collection": ..., "pipeline": [...]}`, u objeto de una sola etapa. Si el JSON trae
el campo `collection`, se puede omitir el argumento `collection` (el argumento tiene
prioridad):

```json
{
  "collection": "users",
  "pipeline": [{"$match": {"status": "active"}}, {"$limit": 100}]
}
```

```python
df = extract_aggregate(profile="bnpl", pipeline_file="queries/usuarios_activos.json")
# o el mismo contenido como texto:
df = extract_aggregate(
    profile="bnpl",
    pipeline='{"collection": "users", "pipeline": [{"$limit": 100}]}',
)
```

Reglas y errores:

- Si se pasan los dos, `pipeline` gana y `pipeline_file` se ignora: el archivo no se
  lee ni se valida (una ruta inexistente no lanza nada) y el campo `collection` del
  JSON solo se toma de la fuente que se uso.
- Hay que pasar uno de los dos: si faltan ambos lanza
  `ValueError("Debes pasar pipeline o pipeline_file")`.
- Sin coleccion (ni argumento ni campo en el JSON) lanza `ValueError`.
- Ruta inexistente lanza `FileNotFoundError` con la ruta ya resuelta a absoluta.
- JSON mal formado lanza `json.JSONDecodeError`.

A diferencia de `run_pipeline_from_file`, esta ruta **no** agrega `$limit` ni reintenta
ante fallos de conexion: ejecuta el pipeline tal cual, con los mismos parametros de
guardado (`save_dir`, `save_csv`, etc.).

### run_pipeline_from_file: archivo con $limit y reintentos

`run_pipeline_from_file` lee el pipeline de un `.json`, aplica `$limit` (salvo `full=True`)
y ejecuta con reintentos ante errores de conexion/tunel:

```python
from pathlib import Path

from mongo_extractor import run_pipeline_from_file

df = run_pipeline_from_file(
    Path(r"queries\mongo\mi_pipeline.json"),
    profile="tx",       # default: "tx"
    limit=10,           # default: 10; ignorado si full=True
    full=False,         # True = pipeline completo, sin $limit
    retries=3,
    retry_wait=5.0,
)
```

La coleccion se resuelve desde el argumento `collection` o desde el campo `collection`
del `.json` (el argumento tiene prioridad). Si no hay ninguno, lanza `ValueError`.

Para controlar el pipeline final antes de ejecutarlo (imprimirlo, validarlo, etc.) usa
las piezas por separado:

```python
from mongo_extractor.pipeline_runner import (
    apply_limit,
    read_pipeline_file,
    run_pipeline_with_retries,
)

raw_pipeline, file_collection = read_pipeline_file(Path("mi_pipeline.json"))
final_pipeline = apply_limit(raw_pipeline, 10)   # limit=None -> sin $limit
df = run_pipeline_with_retries(
    profile="tx",
    collection=file_collection or "transactions",
    pipeline=final_pipeline,
)
```

Los reintentos aplican **solo** a errores de conexion/tunel (timeouts, SSH, connection
refused, `ServerSelectionTimeoutError`). Un error del pipeline —operador invalido,
coleccion inexistente— se propaga en el primer intento sin reintentar.

--------------------------------------------------------------------------------
EVENTOS DE ESTADO
--------------------------------------------------------------------------------

Pasa `on_event` para recibir eventos con niveles `DEBUG`, `INFO`, `WARNING` y `ERROR`.

```python
def printer(evt):
    extras = {k: v for k, v in evt.items() if k not in ("ts", "level", "event", "message")}
    print(f'{evt["ts"]} [{evt["level"]}] {evt["event"]}: {evt["message"]} | {extras}')

from mongo_extractor import extract_aggregate, list_profiles

print(list_profiles(on_event=printer))
df = extract_aggregate("tx", "transactions", [{"$limit": 5}], on_event=printer)
```

Eventos: `CONFIG_LOADED`, `PIPELINE_LOADED`, `ALIAS_RESOLVED`, `TUNNEL_START`, `TUNNEL_READY`,
`DB_CONNECT_START`, `DB_CONNECTED`, `QUERY_START`, `QUERY_OK`,
`CONNECTION_CLOSED`, `DONE`, `ERROR`.

`PIPELINE_LOADED` solo se emite cuando el pipeline vino como texto JSON o como archivo,
e incluye `source` (`"text"` o `"file"`), `stages`, `collection` resuelta y —solo para
archivo— `path` con la ruta absoluta ya resuelta. Util para confirmar que una ruta
relativa apunto al archivo correcto:

```text
2026-08-11T10:22:03 [INFO] PIPELINE_LOADED: Pipeline loaded from file. | {'profile': 'bnpl',
'source': 'file', 'path': 'C:\\...\\queries\\mongo\\usuarios_activos.json', 'stages': 2,
'collection': 'users'}
```

--------------------------------------------------------------------------------
CLI
--------------------------------------------------------------------------------

El paquete expone el comando `mongo-extractor`:

```powershell
mongo-extractor ls
mongo-extractor run --profile tx --collection transactions --pipeline "[{\"$limit\": 5}]" --out .\output\result.parquet --fmt parquet
```

`--pipeline` recibe un JSON como string, con los mismos formatos que un `.json`
(array, objeto `{"collection": ..., "pipeline": [...]}`, u objeto de una etapa); en `run`
la coleccion siempre sale de `--collection`. Formatos de salida: `csv` y `parquet`.

Para correr un pipeline guardado en archivo usa `run-file` (abajo).

### run-file: pipelines desde un archivo .json

Para pipelines guardados como archivo `.json` (con `$limit` automatico para pruebas rapidas,
reintentos ante fallas de conexion/tunel y eventos en vivo):

```powershell
mongo-extractor run-file queries\mongo\mi_pipeline.json
mongo-extractor run-file queries\mongo\mi_pipeline.json --print-pipeline --dry-run
mongo-extractor run-file queries\mongo\mi_pipeline.json --full --output data\output\resultado.csv
mongo-extractor run-file queries\mongo\mi_pipeline.json --profile bnpl --collection users --limit 50
```

Opciones:

| Opcion             | Default              | Descripcion                                                        |
| ------------------ | -------------------- | ------------------------------------------------------------------ |
| `--profile`        | `tx`                 | Alias del perfil de conexion.                                      |
| `--collection`     | campo del `.json`    | Override de la coleccion.                                          |
| `--limit`          | `10`                 | Agrega `$limit` al final del pipeline para prueba rapida.          |
| `--full`           | `False`              | Ejecuta el pipeline completo; ignora `--limit`.                    |
| `--retries`        | `3`                  | Intentos maximos ante fallas de conexion.                          |
| `--retry-wait`     | `5.0`                | Segundos de espera entre reintentos.                               |
| `--output`         | (ninguno)            | Guarda el resultado en CSV (crea el directorio padre).             |
| `--print-pipeline` | `False`              | Imprime el pipeline final (con `$limit` ya aplicado).              |
| `--dry-run`        | `False`              | Solo arma/imprime el pipeline final; no conecta ni ejecuta.        |

El archivo `.json` acepta los mismos formatos que `extract_aggregate` (ver `io.py`):
array directo, objeto `{"collection": ..., "pipeline": [...]}`, u objeto de una sola etapa.
`--collection` tiene prioridad sobre el campo `collection` del archivo.

`run-file` imprime siempre perfil, coleccion, archivo, modo (`FULL` o `$limit N`) y numero
de etapas; al terminar muestra tiempo transcurrido, conteo de documentos/columnas y un
preview de las primeras filas. Los eventos de estado se imprimen en vivo.

--------------------------------------------------------------------------------
ESTRUCTURA DEL PROYECTO
--------------------------------------------------------------------------------

- `config.py`: localiza el env propio y descubre perfiles Mongo por alias.
- `secret_loader.py`: resuelve credenciales desde KeyringManager, variables de sistema y registro de Windows.
- `types.py`: contratos (`MongoConfig`, `SSHTunnelParams`, `SSMTunnelParams`, `AppConfig`).
- `tunnels/ssh.py`: backend SSH (sshtunnel/paramiko).
- `tunnels/ssm.py`: backend AWS SSM port-forward (subprocess `aws ssm start-session` armado desde el perfil; boto3 solo para terminar la sesion).
- `tunnel.py`: dispatcher unificado segun `TUNNEL` del perfil.
- `extractor.py`: `list_profiles`, `extract_aggregate` (acepta `pipeline` como lista o texto JSON, o `pipeline_file`).
- `pipeline_runner.py`: ejecuta pipelines leidos de un archivo `.json`, con `$limit` automatico y
  reintentos ante errores de conexion/tunel (usado por `cli.py run-file` y por runners externos).
  Piezas reutilizables: `apply_limit`, `run_pipeline_with_retries`, `run_pipeline_from_file`,
  `is_connection_error`, `default_event_printer`, `print_result` (mas `read_pipeline_file`
  reexportado desde `io.py`).
- `io.py`: lectura/parseo de pipelines JSON (`parse_pipeline_json`, `read_pipeline_file`,
  `resolve_pipeline_path`) y utilidades de escritura.
- `cli.py`: entrypoint de CLI (`ls`, `run`, `run-file`).

API publica reexportada en `mongo_extractor/__init__.py`: `list_profiles`,
`extract_aggregate`, `run_pipeline_from_file`.

--------------------------------------------------------------------------------
TROUBLESHOOTING
--------------------------------------------------------------------------------

- SSH auth falla: revisa `SSH_USER`, `SSH_KEY_PATH` y permisos del `.pem`.
- SSM no levanta: `aws sso login` reciente, IAM con permisos para `ssm:StartSession`, y revisa `SSM_TARGET` (instance-id). El error ahora trae la salida del forwarder, que suele decir la causa exacta.
- **`El puerto local N ya esta ocupado`** (ruta SSM): hay un forwarder huerfano de una corrida anterior con el puerto tomado. `LOCAL_PORT` es fijo, asi que el tunel nuevo no podria hacer bind y el cliente acabaria hablando con el tunel viejo —posiblemente contra otro cluster— sin ningun error. Por eso se falla en vez de continuar. Liberalo:

  ```powershell
  Get-NetTCPConnection -LocalPort <N> -State Listen | Select-Object OwningProcess
  Get-Process aws, session-manager-plugin | Stop-Process -Force
  ```

  Los huerfanos ya no deberian aparecer: al salir del contexto se mata el arbol completo de procesos. Si vuelven, es porque el proceso murio de una forma que no dejo correr el cierre (un `kill -9`, cerrar la terminal a la fuerza).
- **Destino efectivo de un perfil SSM:** el comando se arma desde `AWS_REGION`, `SSM_TARGET`, `REMOTE_HOST`, `REMOTE_PORT` y `LOCAL_PORT`, asi que el perfil es la unica fuente de verdad del destino. La excepcion es un perfil que todavia traiga `SSM_COMMAND`: ahi manda lo que va dentro de ese comando y el WARNING al abrir el tunel lo recuerda.
- **Sesiones de SSM en AWS:** cada tunel abre **una** sesion y la termina explicitamente al salir del contexto. Antes se abrian dos —una por `boto3.start_session`, que no reenviaba nada, y la del subprocess, que si— y solo se cerraba la primera; la que trabajaba sobrevivia a que se matara el proceso local y quedaba `Active` ~20 min hasta que el idle timeout de AWS la barria. Para auditar:

  ```powershell
  aws ssm describe-sessions --state Active --region us-east-2
  ```

  Si al terminar una corrida queda alguna `Active`, es que no se pudo parsear el `SessionId` de la salida del forwarder; se avisa con WARNING y el cierre queda a cargo del idle timeout.
- Mongo ping timeout: el WARMUP_S puede ser insuficiente; sube `MONGO__<alias>__WARMUP_S`.
- Password con caracteres especiales: el extractor aplica URL-encoding al sustituir en el URI; no escapes manualmente en el secreto.
- Alias no existe: revisa con `list_profiles()` y confirma el bloque en `.env.mongo_extractor`.
- `run-file` falla sin reintentar: el error no es de conexion (operador invalido, coleccion
  inexistente). Valida el pipeline con `--print-pipeline --dry-run`.
- `run-file` devuelve pocas filas: por default agrega `$limit 10`; usa `--full` o `--limit N`.

--------------------------------------------------------------------------------
SEGURIDAD
--------------------------------------------------------------------------------

- No commitear `.env.mongo_extractor`.
- No guardar `USER/PASSWORD` en el env del repo.
- Usar variables de sistema, KeyringManager o secretos del runtime.
- Mantener privilegios minimos en Mongo y en IAM (SSM).
- La libreria no imprime ni loggea credenciales.
