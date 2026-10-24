# Uso desde cada harness

La integración distribuida es CLI + instrucciones/skill, no plugin, MCP ni tool nativa. No hay instalador automático. Antes de modificar configuración, lee la documentación de la versión destino y crea backups exclusivos. No se cambió ningún harness durante la preparación.

## Compartir una sola instalación

Conserva el directorio del paquete en una ruta estable elegida por el propietario. Ejecuta su history_search.py por ruta absoluta. En la instalación de la skill deben vivir SKILL.md y su referencia local references/usage.md; no copies solo el Markdown dejando enlaces rotos. Esa referencia indica cómo localizar el paquete y seleccionar el store. Ajusta una instrucción local con la ruta absoluta aprobada, nunca con credenciales.

El nombre de la carpeta que expongas debe ser agent-history-search, igual que el frontmatter. Usa un enlace al directorio skill/ del paquete con ese nombre, o registra esa ruta específica si el harness lo admite. No expongas toda una colección de skills ajenas para añadir una sola.

## Pi terminal

Según la documentación instalada consultada, carga skills desde ~/.pi/agent/skills/ y ~/.agents/skills/, además de las del proyecto sujeto a confianza. Puedes enlazar la carpeta skill/ del paquete como ~/.pi/agent/skills/agent-history-search. En Windows, una junction evita copiar; en Unix, symlink. Comprueba que el destino no exista antes. No alteres trust ni instrucciones globales completas.

Tras una configuración autorizada, usa /reload en Pi y comprueba que la skill se descubre con /skill:agent-history-search. La forma exacta debe verificarse en la versión instalada antes de automatizarla. La skill invocará la CLI por las herramientas normales del agente. No registra history_search como herramienta nativa.

Store habitual de Pi: ~/.pi/agent/sessions. Es una sugerencia de formato, no selección automática. Si el agentDir está personalizado, usa ese directorio de sesiones. Para omp, selecciona explícitamente `--host omp` y su propio directorio de sesiones; el lector acepta el mismo formato JSONL v2/v3. No mezcles almacenes.

## OpenCode oc2

El wiring local consultado documenta **oc2/v2**, no OpenCode v1: lee las carpetas skill/skills del configdir y rutas concretas del array skills de opencode.json. En la instalación de referencia ese configdir es ~/.config/opencode-v2. Esto es evidencia de esa instalación, no una promesa universal.

Si la versión destino confirma el mismo contrato, añade únicamente la ruta estable de skill/ del paquete al array existente, conservando todos los demás valores. Fragmento orientativo, no reemplazo de archivo completo:

~~~json
{
  "skills": ["/ruta/estable/agent-history-search/skill"]
}
~~~

Si ese oc2 deriva el identificador del nombre de carpeta en vez del frontmatter, expón primero un enlace llamado agent-history-search y registra ese enlace. No copies este bloque encima de la configuración existente. Si no existe la clave, no fuerces un formato adivinado: consulta el esquema de esa versión. Tras aprobación, reinicia la sesión del harness y verifica descubrimiento. No hay integración nativa ni acceso nuevo a cuentas.

Store de referencia: ~/.local/share/opencode/opencode.db con session_v2 y session_message. Puede variar; --store siempre explícito. Solo snapshots SQLite coherentes sin WAL, no bases v1/session-message-part.

## Pi Pocket

Pocket comparte con Pi los archivos de skills; docs/customizing.md indica que vuelve a leerlos en un plazo de unos 30 segundos y que las skills de proyecto requieren confianza. No hace falta editar Pocket, activar un drop-in ni reiniciar el servidor por añadir una skill.

En un Pocket que ya ofrece history_search, utiliza esa herramienta primero. Esta CLI no debe reemplazarla por un error, timeout o resultado vacío. No se instala una segunda tool con ese nombre: en el checkout consultado el nombre está reservado.

Para uso offline independiente, selecciona un snapshot de la instancia autorizada, no la base de otra persona. PI_POCKET_DIR puede cambiar dónde está la instancia; esta CLI no lee esa variable para adivinarla. La herramienta nativa conoce su instancia y sigue siendo preferible para trabajo en vivo.

Exportación oficial para una persona: Menu → Export as Markdown o /export. No se automatiza acceso autenticado ni se parsea esa exportación fingiendo equivalencia de IDs. No se afirma que exista una API oficial de búsqueda portable.

## Codex y Claude Code

Ambos usan la misma CLI, sin plugin ni herramienta nativa. Expón la carpeta
`skill/` como `agent-history-search` mediante el mecanismo de skills que admita
la versión instalada. En la máquina de referencia existen `~/.codex/skills` y
`~/.claude/skills`; verifica descubrimiento en una sesión nueva, no presupongas
que todas las versiones cargan esas rutas.

Codex: `--host codex --store /ruta/.codex/sessions`. Claude Code:
`--host claude --store /ruta/.claude/projects`. Para listar usa `list`; para buscar,
`search --query "tema"`; para leer, `read --session ID-EXACTO --store ARCHIVO-JSONL`.
Usa la ruta devuelta por listado para evitar escanear todo el perfil. Los archivos
archivados, perfiles adicionales y subagentes anidados requieren rutas explícitas.
Solo se leen mensajes user/assistant, sin herramientas ni razonamiento; no se
verifica rama activa ni selección en memoria. No se consulta ningún servicio cloud.

## Verificación sin datos personales

1. Ejecuta la suite offline.
2. Usa --plan con el anfitrión y store aprobados; comprueba fuente y ruta.
3. Verifica descubrimiento de la skill en una sesión nueva o recargada.
4. Solo con permiso de lectura real, ejecuta list con limit 1, timeout corto y snapshot coherente. No registres la salida privada en el repositorio.
5. Informa resultados y límites. Que un modelo lea una skill una vez no garantiza que la use siempre.
