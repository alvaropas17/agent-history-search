# Procedencia, licencia y auditoría de alcance

## Corrección de la conclusión inicial

La ausencia de LICENSE en una carpeta no demuestra que un script sea de terceros ni impide reutilizar código propio. La primera entrega fue demasiado conservadora al detenerse ahí. Esta revisión separa los hechos de las inferencias.

## Evidencia de procedencia disponible

Se inspeccionaron history-route.py, pocket-history.py, oc2_sessions.py, omp_sessions.py y _sessions_common.py en las skills locales. Los encabezados y referencias muestran helpers especializados en esta instalación, pero no contienen una atribución upstream que demuestre autoría o licencia. Ambos directorios de skills no son repositorios Git según git rev-parse, de modo que no hay historial de commits local directo para verificar autores.

Las dos búsquedas acotadas en el historial Pocket seleccionado (oc2_sessions.py y oc2-sessions) no devolvieron evidencia. No se cambió a otro almacén ni se interpretó eso como prueba de que su creación no ocurrió. El catálogo de wiring documenta integración local de esos helpers, pero instalación/adaptación local no prueba titularidad de todo su código.

**Conclusión verificada:** son lectores instalados localmente; no se encontró procedencia legal suficiente para atribuirlos a un upstream o al usuario. **Inferencia limitada:** su especialización y documentación son compatibles con desarrollo/adaptación local, pero no bastan para certificar autoría. No se afirma que tengan una licencia restrictiva.

## Implementación independiente entregada

Se sustituyó el wrapper por lectores nuevos, pequeños y autónomos. No se copiaron los lectores existentes, su router ni la extensión de Pocket. Se consultaron sus contratos y formatos, documentación de Pi/Pocket y metadatos de esquema; las funciones nuevas no dependen de sus archivos ni rutas. Los nombres de tablas/campos y los formatos interoperables se conservan por necesidad de compatibilidad, no como una copia de implementación.

La base Pocket declara MIT (copyright Tanner Middleton 2026). No se copió su extensión ni se aplica su licencia a otros archivos por cercanía. No se copiaron las skills consultadas ni se incorporaron sus textos al runtime. No hay código de terceros vendorizado que exija añadir sus notices al paquete actual; si se añade en el futuro, debe auditarse antes.

## Licencia MIT aprobada

La propuesta inicial quedó resuelta el 17-10-2026: el usuario autorizó expresamente aplicar MIT al código y la documentación nuevos y confirmó que es el titular. Se usa su identificador verificado alvaropas17, sin email: Copyright (c) 2026 alvaropas17. LICENSE contiene el texto MIT definitivo y el manifiesto 0.2.1 declara MIT. Esta autorización no relicencia código de terceros. No se requiere decidir sobre los lectores antiguos para ejecutar o distribuir esta implementación independiente.

LICENSE-MIT.proposed.txt se conserva como borrador histórico local, excluido del ZIP 0.2.1. El ZIP 0.2.0 no se sobrescribe. La autorización de licencia no constituye permiso de publicación: no se hizo commit, push ni publicación.

## Inspección de datos autorizada y limitada

Única inspección directa de los almacenes: PRAGMA table_info de tablas conocidas en oc2 y Pocket, en modo readonly. Se imprimieron únicamente columnas/tipos. No se buscaron conversaciones reales con la nueva CLI, no se volcaron registros privados y no se copiaron bases. Las búsquedas de procedencia se hicieron con history_search en Pocket y no recuperaron resultados.

## Controles y pruebas

Validación de parámetros, igualdad exacta de IDs, SQL parametrizado para valores, identificadores SQL constantes, separación de proceso sin shell, timeout y presupuestos de entrada/salida. SQLite immutable evita escrituras auxiliares; ante WAL se rechaza para no ocultar datos recientes. Los snapshots deben ser coherentes y propiedad del usuario. No hay autenticación, red, instalación ni cambio de permisos.

Las pruebas offline generan sus propias bases/JSONL, comparan hashes, verifican rechazo de escrituras, fuentes explícitas, ramas, formatos inválidos, límites y el worker real. No prueban todos los estados de concurrencia ni garantizan carga automática de la skill. No se verificaron guías OWASP actuales en red; se aplicaron principios conocidos, sin afirmación de certificación.

## Límites de validación

Probado en Python 3.14 sobre Windows. Código sin dependencias externas y rutas pathlib, pero macOS/Linux y Python 3.10 requieren ejecutar la suite allí antes de afirmarlos como validados. No se hizo E2E con conversaciones personales ni instalación en harnesses. OpenCode usa un solo esquema verificado; no todos los productos/versiones llamados OpenCode.
