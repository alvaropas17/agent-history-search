# Prompt de configuración copiable

---
Configura este paquete autónomo agent-history-search en la máquina destino. No publiques, hagas commits/push, cambies instalaciones activas ni accedas a cuentas nuevas.

1. Lee README.md, NOTICE.md, docs/ADAPTERS.md y docs/SCHEMAS.md, y revisa el código. MIT está aprobada para el código y la documentación nuevos, con Copyright (c) 2026 alvaropas17 en LICENSE. Conserva el aviso y no asumas derechos para relicenciar terceros. La licencia no autoriza por sí sola publicar desde esta sesión.
2. Identifica SO y harness anfitrión mediante el contexto de sesión: Pi terminal, OpenCode oc2 o Pi Pocket. No lo deduzcas del cwd o del modelo. Si es ambiguo, pide una aclaración. No mezcles omp, OpenCode v1 o instancias.
3. Comprueba Python y sqlite3 instalados; no descargues ni instales nada sin permiso. El paquete funciona directamente, sin pip. Ejecuta pruebas offline con timeout de 20 s. No uses conversaciones reales como fixtures.
4. Propón una carpeta estable y de alcance mínimo. Si ya existe, no sobrescribas ni borres. Conserva una copia exclusiva con fecha de cada archivo que se autorice modificar y muestra el diff. No reemplaces AGENTS.md ni configuraciones completas.
5. Lee la documentación LOCAL de esa versión antes de exponer la skill. En Pi/Pocket puede enlazarse skill/ como agent-history-search en el directorio de skills. En oc2 confirma si usa array skills o carpetas del configdir. No presupongas el formato de OpenCode v1 ni crees un plugin/tool nativo inexistente.
6. Conserva juntas skill/SKILL.md y skill/references/usage.md, o enlaza su carpeta completa. Registra por ruta estable la ubicación del paquete y el intérprete aprobado, no secretos. No cambies permisos, trust, credenciales, MCP ni extensiones activas.
7. La regla de uso debe exigir fuente del anfitrión, store/instancia autorizada y motivo para otra fuente pedida por el usuario. Vacíos, errores y timeouts nunca autorizan fallback. En Pocket usa primero history_search si existe; un error no autoriza bypass con esta CLI.
8. Ejecuta primero --plan. Usa --store explícito y no explores todos los perfiles. La búsqueda real en otro almacén, otra instancia o datos ajenos requiere autorización expresa. No refresques ni crees índices. No inicies nuevos accesos autenticados ni gastos.
9. SQLite requiere snapshot coherente sin WAL porque el lector usa immutable para evitar incluso escrituras SHM. Si hay WAL, no lo borres ni lo omitas: usa la herramienta nativa de Pocket o pide al propietario un snapshot mediante su backup/cierre correcto. No pares servicios ni copies una base en caliente por tu cuenta. Para Pi acota a un proyecto si hay más de 500 archivos.
10. Si se autoriza una comprobación con datos reales, haz list --limit 1 --timeout 5; evita volcar contenido. Revisa status, incomplete y coverage. No guardes salidas personales en el paquete ni las subas. Las fechas son UTC.
11. Verifica el mecanismo de recarga del harness y pide al usuario el reinicio cuando haga falta; no reinicies Pocket. Entrega archivos modificados, backups, pruebas y limitaciones. No prometas tool nativa ni uso automático fiable por parte de cualquier IA.
---
