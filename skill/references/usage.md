# Uso de la CLI aprobada

La skill acompaña al paquete agent-history-search. No instala una herramienta nativa. El instalador debe registrar la ruta absoluta de history_search.py y el intérprete aprobado en instrucciones locales, sin sustituir instrucciones existentes. Si esa ruta no está disponible, pide su ubicación; no busques perfiles ni ejecutes un script del mismo nombre sin revisar su procedencia.

Consulta primero --help y README.md del paquete. Ejemplos conceptuales (reemplaza rutas/IDs por los autorizados):

~~~sh
python -B /ruta/agent-history-search/history_search.py list --host pi --store /ruta/sessions --plan
python -B /ruta/agent-history-search/history_search.py search --host pi --store /ruta/sessions --query "tema" --limit 3 --context 500 --timeout 10
python -B /ruta/agent-history-search/history_search.py expand --host opencode --store /ruta/snapshot.db --session ID-EXACTO --entry ID-MENSAJE
~~~

Para Codex usa `--host codex --store /ruta/.codex/sessions`; para Claude Code,
`--host claude --store /ruta/.claude/projects`; para omp, `--host omp` con su
directorio de sesiones. También aceptan un archivo JSONL concreto. `read` es alias
de `expand`: exige `--session ID-EXACTO`. Para «hace dos conversaciones», lista
sesiones, excluye la actual si aparece y lee la segunda anterior con el ID y la
ruta `store` devueltos. El orden de listado es modificación, no creación.

~~~sh
python -B /ruta/agent-history-search/history_search.py list --host claude --store /ruta/.claude/projects --limit 5
python -B /ruta/agent-history-search/history_search.py search --host codex --store /ruta/.codex/sessions --query "tema"
python -B /ruta/agent-history-search/history_search.py read --host codex --store /ruta/rollout.jsonl --session ID-EXACTO --context 3000
~~~

Para cambiar fuente añade --source y --reason con la petición explícita; conserva --host real. Fechas inclusivas UTC. Un vacío no cambia de fuente. Los IDs de Pocket son enteros positivos; --session corresponde a conversation_id y --entry a entry_id. expand puede contener varios mensajes de una entrada: message_index permite distinguirlos.

No ejecutes scripts externos del antiguo scaffold. Este paquete incluye sus propios lectores. Para SQLite exige snapshot coherente sin WAL y no crea ni refresca índices. En Pocket en vivo prefiere la herramienta nativa. Los límites y las omisiones se anuncian; no conviertas un resultado incompleto en afirmación de ausencia.
