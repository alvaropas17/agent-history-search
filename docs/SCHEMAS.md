# Contratos de lectura soportados

Los formatos se verifican antes de consultar. No se prueban versiones futuras por inferencia. Los fixtures son sintéticos y no contienen datos del usuario.

## Pi terminal

Referencia de formato: documentación de la versión instalada de @earendil-works/pi-coding-agent, docs/session-format.md. Referencia pública indicada por esa documentación: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts . No se incluye código de ese proyecto.

JSONL v2/v3 con cabecera type=session, version, id y timestamp; cada entrada posterior tiene id y parentId. Mensajes type=message contienen message.role y message.content como string o bloques type=text. El índice de la implementación instalada reconstruye la hoja usando la última entrada del archivo; se verificó esa semántica en SessionManager._buildIndex.

El lector recorre esa hoja hasta parentId=null, detecta ciclos/padres ausentes y conserva solo mensajes user/assistant. No aplica compaction/context_edit ni combina parentSession entre archivos. Rechaza v1 en vez de migrarla. Para expandir de forma inequívoca usa el archivo exacto, no un directorio con varias copias de la misma sesión. Las sesiones vacías se pueden listar.

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

## Frescura

Todos los backends leen directamente del almacén seleccionado; no hay caché ni índice. SQLite exige snapshot sin WAL y comprueba metadatos de archivo antes/después. Pi verifica cada archivo sin cambios durante la lectura. observed_at solo indica la hora de consulta, no la fecha del último mensaje ni la frescura de un snapshot externo.
