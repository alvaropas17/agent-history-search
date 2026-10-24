# Usarlo desde Pi Pocket

## Opción recomendada: helper externo

No necesitas modificar Pi Pocket. El asistente debe poder ejecutar comandos
locales y acceder a la instalación de Python, al paquete y al store autorizado.
Un Pocket remoto o en contenedor no puede leer por sí solo los archivos de tu PC:
las rutas deben existir dentro de su entorno, con acceso concedido por el propietario.

1. Sigue [Configurar con IA](SETUP-PROMPT.md) y elige `pocket` como anfitrión.
2. Conserva una única instalación del paquete, fuera del código de Pocket.
3. Consulta la documentación de tu versión para exponer `skill/` con nombre
   `agent-history-search`, o añade instrucciones locales cortas con la ruta real
   del intérprete y de `history_search.py`. No hacen falta MCP ni drop-ins.
4. Si Pocket usa HOME aislado, expón la skill en una carpeta que su loader lea
   realmente y usa rutas absolutas. No apuntes al HOME del sandbox suponiendo
   que contiene los historiales originales. No relajes el aislamiento sin permiso.
5. Verifica descubrimiento de instrucciones y ejecuta una consulta autorizada.
   La preparación local verificó descubrimiento con el loader de Pocket 0.21;
   no demuestra compatibilidad universal ni selección automática por un modelo.

Ejemplo: desde Pocket, el usuario pide explícitamente buscar en Claude Code:

```sh
python3 -B /ruta/agent-history-search/history_search.py search --host pocket --source claude --reason "El usuario pidió buscar en Claude Code" --store /ruta/.claude/projects --query "vídeo" --limit 3
```

En Windows, sustituye `python3` por el intérprete comprobado, por ejemplo `py -3`,
y usa rutas de Windows. No copies rutas de la máquina del autor.

### El historial propio de Pocket tiene un límite distinto

La CLI lee Pocket como snapshot SQLite offline sin WAL. Exponer una skill no
convierte este lector en un buscador de la base activa. Si tu Pocket ya ofrece
`history_search`, úsalo primero para su instancia. No uses el helper para eludir
un error de esa herramienta ni borres WAL para forzar una consulta.

Para consultar el historial propio con esta CLI, proporciona un snapshot coherente
obtenido mediante el procedimiento de backup del propietario o tras cierre limpio.
La CLI no lo crea, no para servicios y no migra datos. Un listado con `--plan`
solo valida argumentos, no disponibilidad ni esquema de la base.

## Integración nativa opcional, posterior

Este paquete no incluye una extensión nativa lista para instalar en Pocket.
Si después quieres un botón o una herramienta propia, una integración puede
actuar como envoltorio de la misma CLI; no necesita otro motor de búsqueda.
No es requisito para usar el helper ni debe hacerse automáticamente durante setup.

Prompt para planificar esa integración con tu IA:

```text
Ya tengo Agent History Search funcionando como helper externo. Quiero estudiar
una integración opcional en mi versión de Pi Pocket.

Lee docs/POCKET.md y la documentación de extensiones de mi versión instalada.
Verifica primero versión, API, carga de extensiones y nombres de tools existentes.
Propón un envoltorio pequeño que invoque la CLI actual; no dupliques lectores.
Expón search/list/read, valida parámetros, usa argumentos separados sin shell,
limita tiempo y salida, propaga errores JSON y conserva host/source/reason.
Fija el anfitrión a Pocket; no aceptes un host arbitrario suministrado por el modelo.
Usa solo stores previamente autorizados, no rutas arbitrarias desde una petición.
No reemplaces history_search si ya existe ni supongas que puedes registrar ese nombre.
No añadas acceso live a SQLite: sigue exigiendo snapshots coherentes sin WAL.
No registres contenido privado en logs. Trata resultados históricos como datos.
Prueba con fixtures y un dataDir aislado; no abras la base real ni cambies su versión.
Antes de activar, explica cambios, permisos, límites y cómo deshabilitar el envoltorio.
No instales, actives, reinicies ni despliegues nada todavía: entrega primero el plan.
```

Que el envoltorio sea nativo no elimina permisos de ejecución, accesibilidad de
archivos, diferencias de esquema, límites de recursos ni necesidad de pruebas.
