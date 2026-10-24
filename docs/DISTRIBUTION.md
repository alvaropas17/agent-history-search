# Preparar una distribución local

No hay dependencias Python externas ni instalación necesaria. El ZIP contiene una carpeta agent-history-search/ con la CLI, paquete, documentación, skill y pruebas. Descomprimir y ejecutar history_search.py basta.

## Construcción

~~~sh
python -B tools/build_distribution.py
~~~

El builder utiliza una lista cerrada de archivos; no empaqueta cualquier archivo que aparezca en el repositorio. Cada archivo debe existir y no ser symlink. Escribe en modo exclusivo en dist/agent-history-search-0.2.2.zip y su .sha256. Si ya existen, se detiene: no sobrescribe ni borra la versión previa. Para otro destino usa --output /ruta/nueva.zip. Los nombres incluidos son relativos, sin traversal. MANIFEST.json contiene ruta y SHA-256 por archivo.

No se ejecutan consultas de historial ni red durante el build. scaffold-backup-v1/ conserva la entrega anterior solo en la máquina de trabajo y nunca se distribuye. tools/inspect_schema.py y tools/snapshot_scaffold.py tampoco forman parte del ZIP. Las pruebas solo generan fixtures temporales sintéticos.

## Configuración por IA y Pocket

El ZIP incluye `docs/SETUP-PROMPT.md` para configurar el helper externo desde
cualquier harness compatible, y `docs/POCKET.md` para usarlo desde Pocket o
planificar después un envoltorio nativo opcional. No incluye historiales, rutas
autorizadas de un usuario ni una extensión Pocket lista para activar. Las rutas y
los permisos se acuerdan durante la configuración local. No requiere servicios.

## Antes de enviar a otra persona

1. Conservar LICENSE y su aviso Copyright (c) 2026 alvaropas17: MIT ya fue aprobada por el titular para el código y la documentación nuevos.
2. Ejecutar tests en el SO y Python de destino.
3. Revisar contenido del ZIP y su manifiesto; no añadir snapshots, config o resultados privados.
4. Confirmar que no se pretende soportar esquemas distintos de los documentados.
5. Distribuir solo con autorización. Este trabajo prepara artefactos locales; no publica ni crea repositorios remotos.

El ZIP 0.2.1 incluye LICENSE con MIT aprobada y el manifiesto correspondiente. El borrador LICENSE-MIT.proposed.txt queda fuera de la lista de distribución, solo como histórico local. El ZIP 0.2.0 se conserva sin sobrescribir. Preparar esta distribución local no autoriza publicar un release ni hacer commit o push.
