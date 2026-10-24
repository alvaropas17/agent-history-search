# Contratos de lectura soportados

Los formatos se verifican antes de consultar. No se prueban versiones futuras por inferencia. Los fixtures son sintéticos y no contienen datos del usuario.

## Pi terminal

Referencia de formato: documentación de la versión instalada de @earendil-works/pi-coding-agent, docs/session-format.md. Referencia pública indicada por esa documentación: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts . No se incluye código de ese proyecto.

JSONL v2/v3 con cabecera type=session, version, id y timestamp; cada entrada posterior tiene id y parentId. Mensajes type=message contienen message.role y message.content como string o bloques type=text. El índice de la implementación instalada reconstruye la hoja usando la última entrada del archivo; se verificó esa semántica en SessionManager._buildIndex.

El lector recorre esa hoja hasta parentId=null, detecta ciclos/padres ausentes y, en modo texto, conserva solo mensajes user/assistant. omp (Oh My Pi) usa este lector y puede anteponer un registro type=title sin id a la cabecera; list conserva ese título. No aplica compaction/context_edit ni combina parentSession entre archivos. Rechaza v1 en vez de migrarla. Para expandir de forma inequívoca usa el archivo exacto, no un directorio con varias copias de la misma sesión. Las sesiones vacías se pueden listar.

## OpenCode oc2 — exclusivamente session_v2

Inspección readonly de PRAGMA table_info realizada el 17-10-2026 sobre la instalación local. No se leyeron contenidos de mensajes ni se incluyeron datos reales en fixtures.

Columnas requeridas:

- session_v2: id, title, directory, time_updated.
- session_message: id, session_id, type, seq, time_created, data.

Los tiempos son Unix en milisegundos. El contrato JSON de usuario usa data.text; assistant usa data.content con bloques type=text. Este contrato de contenido se contrastó con el lector local existente, no con muestras privadas. Otros esquemas (por ejemplo session/message/part de otros OpenCode) se rechazan. Los prefijos de sesión no se resuelven: se usa igualdad exacta.

No interpreta session_v2.revert, fork_boundary, parent_id o herencia entre sesiones. Es búsqueda de mensajes guardados, no reproducción del contexto activo. No usa el índice FTS auxiliar del helper anterior, por lo que puede ser más lenta en bases grandes; filas y tiempo están acotados.

## Pi Pocket / pi-durable

Se consultaron completos docs/index.md, docs/customizing.md, docs/extensions.md y docs/history-tool-calls.md de Pocket. Se inspeccionaron las declaraciones de esquema y migraciones SQLite de la versión instalada de pi-durable, y se corroboraron nombres/columnas con PRAGMA table_info readonly.

Tabla requerida: entries(id, conversation_id, record). Las columnas head y commit_seq existen en la instalación inspeccionada, pero NO se usan para inferir rama activa. record es objeto JSON; model es lista de mensajes; role user/assistant y content string/bloques text. Las referencias incluyen conversation_id, entry_id y message_index base cero.

No consume documents, config, secretos, tareas ni datos de permisos. No incluye conversaciones sin texto de usuario/asistente en list. Subagentes son conversaciones separadas. Es un lector de almacenamiento versionado por forma, no una interfaz estable del producto: ante cambios de estructura debe fallar o actualizarse con fixtures nuevos.

Para una exportación oficial legible por personas, Pocket ofrece Menu → Export as Markdown y /export. Esa exportación no preserva exactamente las referencias de almacenamiento y no se presenta como backend de esta CLI.

## Herramientas — modo opt-in --tool-calls

El modo texto sigue excluyendo llamadas y resultados. El modo herramientas consulta argumentos y texto de resultados, nunca imágenes. Las fixtures sintéticas cubren los seis formatos; no prueban compatibilidad con versiones futuras ni validación en todos los stores reales.

- **Pi / omp (Oh My Pi):** bloques assistant `toolCall` (`id`, `name`, `arguments`) y mensajes `toolResult` (`toolCallId`, `toolName`, `content`). Fallo: `isError=true` o entero `details.exitCode` distinto de cero. Se conserva la cadena de padres de la hoja persistida.
- **Claude Code:** registros user/assistant con bloques `tool_use` (`id`, `name`, `input`) y `tool_result` (`tool_use_id`, `content`, `is_error`). Fallo solo si `is_error=true`. Referencia: uuid del registro o número de línea.
- **Codex:** registros `response_item` con payload `function_call`/`custom_tool_call` (`call_id`, `name`, `arguments`/`input`) y sus `function_call_output`/`custom_tool_call_output` (`call_id`, `output`). Fallo por entero `metadata.exit_code` (o `exit_code` en objeto raíz) distinto de cero en JSON, o línea `Process exited with code N` / `Exit code: N` en los primeros 4000 caracteres. La palabra «error» no es evidencia. Referencia: ordinal o número de línea.
- **OpenCode oc2:** bloques assistant `type=tool` con `id`, `name`, `state.input`, `state.status`, `state.content` y `state.error.message`; timestamp `time.created` o tiempo del mensaje. `completed`/`error` conservan resultado; `error` aporta evidencia `status=error`. `running` y otros estados no terminales quedan sin resultado. Llamada y resultado viven en el mismo bloque.
- **Pocket:** mensajes Pi en `record.model`; empareja llamadas/resultados dentro de cada conversación, también entre entradas. Nunca une el mismo call ID de conversaciones distintas. Referencias: entry_id y message_index. No verifica rama activa ni herencia entre conversaciones.

Se empareja por call ID, no por proximidad; resultados huérfanos llevan `missing: call_block_missing`. Estados: `failure` con `evidence` explícita; `no_failure_recorded` con resultado sin fallo reconocido (no prueba éxito); `no_result_stored` sin resultado dentro del alcance leído. `evidence` es null si no hay fallo reconocido. `--tool` exige igualdad exacta; query busca subcadena literal sin distinguir mayúsculas en argumentos/resultados. `--entry` acepta referencias de cualquiera de los dos lados. Las fechas usan timestamp de llamada, o del resultado si falta. Los límites de lectura pueden dejar pares incompletos.

Argumentos/resultados pueden contener datos sensibles y son datos no confiables. No se ejecutan instrucciones recuperadas ni se deduce estado actual del sistema. SQLite mantiene el requisito de snapshot coherente sin WAL; en Pocket en vivo se usa primero history_search nativo.

## Frescura

Todos los backends leen directamente del almacén seleccionado; no hay caché ni índice. SQLite exige snapshot sin WAL y comprueba metadatos de archivo antes/después. Pi verifica cada archivo sin cambios durante la lectura. observed_at solo indica la hora de consulta, no la fecha del último mensaje ni la frescura de un snapshot externo.
