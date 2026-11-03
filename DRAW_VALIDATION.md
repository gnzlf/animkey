# Draw: apertura e interfaz

## Corrección de entrada — 1 noviembre 2026

La barra de controles pertenece ahora a la ventana del lienzo para conservar
su posición encima al activar el dibujo. Los eventos del lápiz enviados al
viewport se redirigen únicamente desde la vista, excluyendo botones y otros
controles. Tanto el filtro de Qt como Windows Ink comprueban la barra visible,
las ventanas emergentes y los diálogos antes de iniciar un trazo. Nav deja pasar
la entrada del lienzo conservando los botones de su ventana de controles.

Se reprodujeron dos fallos antes de cambiar el código: un toque sobre la barra
redirigido al viewport iniciaba un trazo, y un evento recibido por otro botón
era capturado como dibujo. Ambos pasan tras la corrección. Pasaron 13 pruebas
de ventana/entrada y las 18 pruebas de presión, dibujo, eraser, undo y resize.
La prueba de clics usa hit-testing y eventos Qt sintéticos en un proceso
offscreen independiente; no mueve el mouse ni interviene la sesión de Maya.
No se probó un dispositivo físico de lápiz.

Evidencia: `dist/brush-input/before-tests.log`, `window-tests.log`,
`rendering-tests.log` y `validation.json`.

## Validación anterior

Actualizado e instalado el 22 de octubre de 2026 en Maya 2024.2.

- Los controles usan el mismo GlassPanel, TitleBar, WindowControl, tipografía y
  material nativo de Settings. La paleta se puede mover y colapsar.
- El canvas conserva la transparencia sobre el viewport; el blur se aplica
  solamente a la ventana de controles.
- Se eliminó el repintado incondicional del canvas y se reutiliza el widget del
  viewport, consultando cambios de panel a una frecuencia menor.
- Los trazos guardados se suavizan al renderizar el frame necesario; abrir Draw
  ya no prepara todos los frames a un tamaño temporal.
- Abrir Draw no reconstruye las keys auxiliares ni selecciona el nodo de dibujo.
- Ocultarlo detiene su sincronización y retira sus filtros de entrada. Navegar
  usa WindowTransparentForInput para que el overlay deje pasar los clics a Maya.
- Se conservaron pincel, goma, selección, transformaciones, onion skin, color,
  tamaño, guardado/carga y playblast. Los controles separados no capturan el
  lápiz como si fueran parte del canvas.

## Validación

- `dist/draw-window-final-tests.log`: **8/8** pruebas de interfaz, apertura,
  selección, undo, datos guardados, cierre, navegación, sincronización y carga.
- `dist/draw-rendering-tests.log`: **18/18** pruebas de dibujo, presión de lápiz,
  borrado, transformaciones y conservación de proporciones al redimensionar.
- `dist/draw-ui-validated.json`: prueba GUI con `v0030` y perfil temporal de Maya.
  Apertura: **0,075 s**. Intervalo máximo del temporizador de prueba: **0,064 s**,
  comparable con **0,063 s** antes de abrir Draw. Ocultar y reabrir funcionó y
  el estado de la escena permaneció igual. No se guardó la escena.
- `dist/draw-update-reload.json`: instalado y abierto en la sesión principal
  (`v0031` al efectuar la recarga). Apertura: **0,125 s**; intervalo máximo
  observado: **0,081 s**. Controles y canvas visibles; material nativo activo.
  Se conservaron escena, frame, selección, auto-key, undo, estado modified y
  SHA-256 de los dibujos almacenados.
- `dist/draw-installation.json`: copia instalada verificada por SHA-256 y ruta
  del respaldo previo.

Las primeras mediciones con el perfil habitual incluyeron inicialización de
Arnold y callbacks de recuperación de animBot; no permiten atribuir esas pausas
exclusivamente a Draw. La medición final aisló esos trabajos de inicio y se
completó además con una comprobación en la sesión principal ya abierta.

No se probaron otras versiones de Maya ni un dispositivo físico de lápiz en
esta pasada; la batería de presión usa eventos simulados y API de Qt.
