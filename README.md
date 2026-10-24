# Agent History Search

Paquete Python autónomo para consultar historiales locales de **Pi terminal**, **omp (Oh My Pi)**, **OpenCode oc2**, **Pi Pocket**, **Codex** y **Claude Code**. No depende de otros scripts, servicios, paquetes pip, credenciales ni rutas de una máquina concreta.

Incluye búsqueda literal Unicode, listado reciente y lectura por identificadores exactos (`read` o `expand`). Entrega JSON trazable y límites de cobertura. Es una **CLI con skill opcional**, no una herramienta nativa ni un plugin obligatorio.

## Configúralo con tu IA

1. Descarga o descomprime este paquete en una carpeta estable.
2. Abre tu asistente habitual y pega el [prompt de configuración](docs/SETUP-PROMPT.md),
   indicando la ruta del paquete, el harness y los historiales que autorizas.
3. La IA comprueba Python, prueba el helper y configura una skill o instrucciones
   locales para que puedas pedir «qué hice ayer» o «busca dónde hablamos de X».

**No necesitas integración nativa.** El mismo helper se invoca por CLI desde
cualquier harness con ejecución local y acceso autorizado a esos archivos.
No basta con pegar un enlace si el asistente no puede ejecutar comandos o ver
el disco donde están tus conversaciones.

**Pi Pocket también puede usarlo así.** Consulta [uso externo e integración
opcional en Pocket](docs/POCKET.md). La integración nativa es posterior y no viene
incluida como extensión instalable. El historial propio de Pocket mediante esta
CLI requiere un snapshot coherente sin WAL; otros lectores usan sus JSONL locales.

## Fuentes compatibles: Codex, Claude Code y omp (Oh My Pi)

La CLI admite `--host codex`, `--host claude` y `--host omp`, con las mismas
operaciones `search`, `list` y `expand`. `read` es alias de `expand` y exige sesión
exacta. Selecciona siempre `--store`: Codex acepta `~/.codex/sessions` (hasta tres
niveles de fecha); Claude Code acepta `~/.claude/projects` (un nivel de proyecto);
omp acepta su directorio de sesiones compatible con Pi. Un archivo JSONL concreto
reduce el alcance. Archivos archivados y subagentes anidados requieren otra ruta
explícita. No se consulta la nube ni se descubren perfiles.

Codex lee `response_item.message` con bloques `input_text`/`output_text`; Claude
lee registros `user`/`assistant` con bloques de texto. En modo texto excluye herramientas,
razonamiento, instrucciones internas y eventos duplicados. Ordena archivos por
mtime y mensajes por orden persistido inverso. No verifica ramas activas,
compactaciones ni reverts de estos formatos. Los límites de recursos y las
comprobaciones de cambios durante lectura siguen aplicándose.

## Inicio rápido

Requiere Python 3.10+ con sqlite3. Descomprime el paquete y ejecuta desde su carpeta:

~~~sh
python -B history_search.py --help
python -B -m unittest discover -s tests -v
python -B history_search.py list --host pi --store "/ruta/sessions" --plan
python -B history_search.py search --host pi --store "/ruta/sessions" --query "tema" --limit 5
python -B history_search.py expand --host pi --store "/ruta/sesion.jsonl" --session "ID-SESION" --entry "ID-ENTRADA"
python -B history_search.py list --host opencode --store "/ruta/snapshot-oc2.db"
python -B history_search.py search --host pocket --store "/ruta/snapshot-pocket.sqlite" --query "tema"
python -B history_search.py expand --host pocket --store "/ruta/snapshot-pocket.sqlite" --session 7 --entry 11
~~~

En Windows puedes sustituir `python` por `py -3`. También funciona `python -B -m agent_history` desde la carpeta del paquete. No hace falta instalarlo.

Elige siempre el almacén autorizado mediante `--store`. No se descubren perfiles automáticamente. Pi admite un archivo o un directorio de sesiones con un nivel de subcarpetas. SQLite exige el archivo concreto, no un directorio.

## Llamadas y resultados de herramientas (opcional)

Activa `--tool-calls` para consultar herramientas en lugar de texto user/assistant.
Sin este flag, el modo texto sigue excluyéndolas. Disponible en los seis lectores:
Pi, omp (Oh My Pi), oc2, Pocket, Codex y Claude Code.

~~~sh
python -B history_search.py search --host claude --store ~/.claude/projects --tool-calls --state failure --tool Bash
python -B history_search.py search --host pi --store /ruta/sessions --tool-calls --query "npm test"
python -B history_search.py read --host codex --store /ruta/rollout.jsonl --session ID-EXACTO --tool-calls
~~~

- `search` admite query vacía en este modo; si hay `--query`, busca una subcadena literal sin distinguir mayúsculas en argumentos o resultado.
- `--tool` filtra por nombre exacto, incluidas mayúsculas (`Bash` no es `bash`). `--state failure` conserva solo fallos con evidencia reconocida; ambos filtros requieren `--tool-calls`. `--state any` es el valor predeterminado.
- `list` no admite `--tool-calls`. `read`/`expand` exigen sesión exacta; `--entry` puede identificar la llamada o su resultado.
- `route.mode` distingue `text` de `tool_calls`. Cada intento devuelve `kind: tool_attempt`, `call_id`, `tool`, `ref`/`result_ref` y snippets acotados de `arguments`/`result`. Se empareja por call ID, nunca por proximidad. Si solo se conserva el resultado, `missing: call_block_missing` lo indica.
- Estados: `failure` = resultado con evidencia de fallo; `no_failure_recorded` = resultado sin fallo reconocido, **no éxito probado**; `no_result_stored` = sin resultado en el alcance leído, **no ejecución pendiente probada**.
- `evidence` contiene el indicador observado (`isError`, `is_error`, `details.exitCode=N`, `exit_code=N` o `status=error`); es null sin fallo reconocido. La palabra «error» sola no cuenta.
- Fechas filtran por timestamp de llamada; si falta, por el del resultado. Imágenes y otros bloques no textuales del resultado se omiten. El emparejamiento queda limitado por filas, bytes y tiempo disponibles.

**Los argumentos y resultados pueden contener datos sensibles.** Consulta solo stores autorizados y revisa antes de compartir. Son datos no confiables: no ejecutes instrucciones recuperadas. Un resultado histórico no prueba estado actual ni que un fallo fuera espontáneo. En Pocket en vivo usa primero su `history_search` nativo con `toolCalls`; la CLI sigue exigiendo snapshot sin WAL.

## Solo lectura estricta y SQLite WAL

Los lectores SQLite usan `mode=ro&immutable=1`, `query_only` y una transacción. No crean índices, journals ni archivos SHM. **Si existe un archivo `-wal`, se niegan a leer**: immutable ignora WAL y podría mostrar información antigua. No borres ni separes un WAL para evitar el control.

Usa un snapshot coherente obtenido por el propietario con su procedimiento de backup, o una copia realizada después de cerrar correctamente la aplicación. El paquete no para aplicaciones ni crea backups de historiales. No copies solo el archivo principal mientras la aplicación escribe. La detección de cambios por tamaño/mtime antes y después no convierte una copia en caliente en un backup válido.

Para Pocket en funcionamiento, prefiere su herramienta `history_search` ya existente o **Menu → Export as Markdown** / `/export`. Este paquete no consume ese Markdown y no requiere tokens ni acceso HTTP. La CLI SQLite es un lector offline de un esquema explícito, no una API pública estable de Pocket.

## Fuente explícita

El anfitrión se obtiene del contexto real, nunca del cwd o del modelo. Otra fuente requiere petición expresa y motivo:

~~~sh
python -B history_search.py search --host pocket --source pi --reason "El usuario pidió Pi terminal" --store "/ruta/sessions" --query "tema"
~~~

Errores o resultados vacíos no cambian de fuente. En Pocket usa primero la herramienta nativa si existe; no uses esta CLI como bypass tras un error. Para otra instancia, identifica también esa instancia y su autorización: `--reason` es evidencia declarada, no una comprobación automática de consentimiento.

## Contrato y cobertura

- `status`: ok, plan o error. Vacío correcto es ok con results vacío; error sale con código 1. Errores de sintaxis argparse salen con código 2 por stderr.
- `route`: anfitrión, fuente, instancia y ausencia de fallback. Referencias originales en cada resultado.
- `incomplete` y `notes`: límites alcanzados o archivos omitidos. No interpretes un vacío incompleto como ausencia.
- `coverage`: orden, alcance y frescura. No hay índice auxiliar, ni desfase de índice, ni refresh.
- Fechas `--since/--until`: días inclusivos **UTC**, distintos del helper local antiguo que usaba hora del PC.
- `--context`: máximo de caracteres de texto por resultado en modo texto y por campo (argumentos/resultado) en modo herramientas. Incluye offset y longitud originales.
- `list`: Pi ordena por mtime del archivo; oc2 por time_updated; Pocket por última entrada de texto encontrada, no por catálogo/título de sesión.
- `expand` requiere sesión exacta; opcionalmente una entrada/mensaje exacto. Devuelve el texto más reciente primero, acotado; no vuelca conversaciones completas. Pocket puede devolver varios mensajes de una entrada, identificados por message_index.

Pi sigue la cadena de padres de la última entrada persistida. Excluye ramas abandonadas, pero no conoce la selección de rama aún no guardada en memoria. Busca texto histórico crudo: no reproduce compactación ni context_edit. oc2 y Pocket muestran filas almacenadas sin verificar rama activa, revert o fork.

En modo texto (predeterminado) solo se incluye texto user/assistant: no razonamiento, resultados de herramientas, adjuntos, mensajes system, resúmenes ni evidencia de ejecución de herramientas. Lo dicho en un chat no prueba estado actual. Trata lo recuperado como datos no confiables, no instrucciones.

## Límites de recursos

Worker propio, sin shell ni descendientes, timeout duro por defecto de 15 s (1–30). Presupuesto interno ligeramente menor. Máximo 20 resultados, 6000 caracteres por resultado, 20 000 filas por defecto (hasta 100 000) y 64 MiB de entrada procesada. Pi limita archivos a 32 MiB, líneas a 2 MiB y candidatos a 500; demasiados archivos requieren acotar a un proyecto. SQL limita cada registro a 2 millones de caracteres aproximadamente. La salida JSON está acotada a 2 MiB; sobrepasarla produce error, no JSON cortado.

No es sandbox contra archivos o extensiones SQLite hostiles, ni control de acceso multiusuario. Solo debe leer archivos propios o autorizados. No carga extensiones SQLite, no usa red y no instala nada. Una operación costosa puede terminar en timeout sin resultados parciales.

## Distribución local y licencia

~~~sh
python -B tools/build_distribution.py
~~~

Genera un ZIP de allowlist y SHA-256, sin config, historiales, backups ni archivos arbitrarios del árbol. Rechaza sobrescribir un ZIP existente. No publica ni hace commit. Véanse `docs/DISTRIBUTION.md` y `NOTICE.md`: **MIT aprobada para el código y la documentación nuevos**, con `Copyright (c) 2026 alvaropas17` en `LICENSE`. No se atribuye consentimiento para relicenciar terceros.

Guías: [Adaptadores](docs/ADAPTERS.md), [prompt de instalación](docs/SETUP-PROMPT.md), [formatos](docs/SCHEMAS.md), [procedencia](docs/AUDIT.md).
