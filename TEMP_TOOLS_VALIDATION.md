# Temp Pivot y Temp Controls — ANIMKEY 1.2.1

Implementación independiente a partir de las dos especificaciones técnicas
suministradas el 22 de octubre de 2026. No requiere instalar animBot.

## Uso

Los botones aparecen juntos en el orden `WS · PIV · TMC · RUL`. Ambos tienen
iconos SVG propios, ventanas de opciones y soporte en ajustes y atajos. El clic
derecho abre la ventana de cada herramienta, sin menús ni submenús. Los atajos
personalizados del usuario tienen prioridad sobre los modificadores integrados.

**PIV** crea un proxy manipulable sin cambiar los pivotes ni el parentado del
rig. Clic: activar/desactivar. Shift: usar el último objeto como ancla sin
transformarlo. Ctrl: centro por extremos de posiciones de transforms.
Ctrl+Shift: pivote fijo en World al recorrer frames. Alt: editar el marco.
Ctrl+Alt: reset. Las variantes Last/Center/World sólo activan una sesión nueva.
La ventana de PIV permite aplicar otro modo a la selección durante una sesión.
La memoria de posición y orientación del pivote vive en la sesión Python.

**TMC** abre Create Temp Control. Ofrece Simple, Group, Aim con IK/Pole Vector/
Twist y FK Chain con orientación World/Parent/Local. Position y Orientation
se configuran por separado; se filtran las opciones incompatibles por familia.
En Relative y Grab Release, el último seleccionado se reserva como referencia
si no se proporciona una por API. Camera prioriza cámara seleccionada y vista
activa antes del fallback de escena. Object sigue al padre original; Child
sigue una copia evaluada de la animación original para evitar un ciclo.

Ctrl: crear Simple Relative; Shift: Object; Ctrl+Shift: Camera; Ctrl+Alt:
World; Alt: Toggle and Bake; Shift+Alt: panel; Ctrl+Shift+Alt: Clear and Bake.
El panel permite activar/desactivar, Bake/Revert, cambiar espacios, editar
apariencia, añadir/quitar controles extra, editar/resetear pivotes, gestionar
miembros Group/FK y añadir/quitar/normalizar transiciones Grab Release.

La ventana organiza estas opciones en Create, Controls y Tools. Reutiliza
SettingsSwitch y los controles visuales de Settings. Controls tiene switches
Enabled y Revert when disabling, un slider de tamaño de forma con porcentaje,
selectores de forma/orientación y acciones Bake & Remove / Revert & Remove
siempre visibles. Tools contiene espacios, extras, pivotes, miembros y archivos;
las opciones de Group/FK aparecen sólo cuando corresponden. Las páginas tienen
scroll y la altura inicial se ajusta al espacio disponible en pantalla.

Los cambios de apariencia conservan la selección y las transformaciones del
rig; el tamaño mantiene las proporciones de cada forma y admite Undo/Redo.
La barra de pestañas se considera interactiva en ContextPopupWindow, evitando
que el arrastre magnético intercepte sus clics. El refresh diferido pertenece
a la ventana y se cancela al cerrarla.

Group recupera el seguimiento promedio de los controles originales de las
primeras versiones de GitHub: el master sigue la animación desde un grupo
independiente horneado y mantiene sus canales locales a cero para añadir offsets.
No se crea una dependencia circular cuando las fuentes incluyen padre e hijo.

Bake Frames es el valor predeterminado. Bake Keys toma tiempos de claves y
dependencias como alternativa explícita; Bake Frames incluye cada
frame entre los extremos de claves y playback. El modo Keys puede cambiar la
interpolación entre muestras. La conversión local compatible copia curvas
directas; las demás rutas evalúan matrices.
El bake final siempre toma cada frame, incluso en montajes antiguos creados
con Keys, y captura el espacio del padre antes de desconectar los controles.
Así conserva el resultado visible y evita el desfase de evaluación de las
capas al hornear padres e hijos juntos.

Los montajes se guardan en la escena con enlaces message a fuentes, controles,
drivers y nodos propios. Sobreviven a renombrados y guardar/reabrir. Al activar
se conservan las redes originales en plugs del mismo tipo/unidades. Revert
reconecta esas redes. Bake escribe una capa override `TempControls_Bake_AnimKey`
que conserva las capas originales. Toggle mantiene el montaje; Clear lo retira
y conserva las capas horneadas. Revert al reactivar recupera los controles
temporales previos; Bake al reactivar vuelve a transferir el rig al montaje.
Los props ajenos parentados bajo controles se separan y conservan su animación
antes de borrar el sistema.

Los archivos de setup usan `.aktc.json`, con muestras de controles, controles
extra y apariencia. Import/Export recuerda la ruta utilizada. Los nombres
de fuentes y namespaces deben existir al importar. El panel ofrece recuperación
del panel antiguo cuando detecta montajes legacy; su motor se conserva en
`tempControlsLegacy.py`.

## Diferencias y límites de fidelidad

- No se suministraron los JSON de curvas NURBS citados por las especificaciones.
  Las 13 formas son geometría propia, con el mismo catálogo funcional; las 24
  orientaciones sí usan los valores documentados. Los iconos son propios de
  ANIMKEY. No se afirma igualdad visual con animBot.
- El setter utiliza una copia de transformación de Maya y corrige el residual
  de traslación por pivotes. La continuidad Euler usa una solución próxima en
  PIV y el filtro Euler de Maya al hornear. No reproduce el Smart Euler del
  proveedor, cuyo contrato figura pendiente en los documentos.
- Aim usa joints, IK RP, Pole Vector y Twist. La distancia se configura antes
  de crear; no incluye el pre-setup interactivo de tres locators del original.
- La implementación Grab Release compensa offsets de referencias y preserva
  poses muestreadas al reconstruir. No se ha demostrado identidad con el
  algoritmo matricial interno de normalización/Y Up original. El panel muestra
  tiempos; no incluye una herramienta de arrastre/nudge de transiciones.
- Las operaciones de membresía Group/FK hornean el resultado al rig y reconstruyen
  el montaje. Esto conserva poses muestreadas y deja capas recuperables; cambia
  la base de Revert de ese montaje respecto de su creación anterior.
- Import/export usa un formato propio. No hay compatibilidad declarada con
  `.anb`. El setup conserva poses muestreadas; no todos los metadatos de tangentes
  o layers originales, ni los receptores internos de edición de pivote.
- La restauración de timeline/editores usa las conexiones previas de Maya.
  Falta una prueba manual con layouts personalizados de Graph Editor y con
  manipulación nativa prolongada. Las pruebas aisladas no son una comparación
  runtime con animBot ni una validación de todos los rigs de producción.

## Validación reproducible

`tests/test_temp_control_motion.py` añade ocho regresiones en Maya 2024:
padres animados, seguimiento Group comparado con el método original de
locators horneados y parentConstraint, offset local cero, cambios del master,
bake de montajes Keys existentes, padre/hijo, Toggle y guardar/reabrir.
Se comparan las matrices de cada frame, con tolerancia de 0,00002.

`tests/preview_temp_tools_maya.py` comprueba los botones reales de la barra,
la apertura por clic derecho sin construir QMenu, los modos de PIV y el cierre.
`tests/verify_temp_tools_native.py` repite la apertura, activación y cierre de
ambas ventanas en un Maya GUI con perfil desechable.
Esta prueba separa la limpieza de ventanas del cierre completo de Maya y deja
el proceso disponible para recoger el informe. En una ejecución con salida
automática, los ocho ciclos finalizaron correctamente y se registró después
una excepción nativa durante el cierre de Maya. El cierre completo de la
aplicación queda sin validar; Maya sin AnimKey también quedó detenido al salir
en la prueba de referencia, sin permitir atribuir el fallo a un componente.

`tests/test_temp_tools_maya.py` pasa 21 casos en Maya 2024, en escenas desechables:
delta matricial de PIV, centro por extremos, padre/hijo, canales bloqueados,
Undo/Redo, Auto Key, cuatro familias, transferencias Bake/Revert, capas
existentes, escala Group, espacios World/Object/Relative/Child/Grab Release,
renombrado y guardar/reabrir, extras, transiciones, pivote compensado/reset,
props ajenos, joints con jointOrient/rotateOrder/pivotes, membresía,
import/export con extras, herencia de posición/orientación FK, borrado de una
fuente, cambio de PIV a TMC conservando la selección del rig y ambas direcciones
de Toggle con Child Space.

Ejecutar con el `mayapy.exe` de Maya:

```powershell
& 'C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe' tests/test_temp_tools_maya.py
```

`tests/preview_temp_tools_maya.py` renderiza los widgets Qt reales en offscreen,
verifica filtros por familia, creación desde el diálogo y limpieza de callbacks,
y construye la barra real para comprobar adyacencia y lanzamiento de TMC.
Las capturas se generan bajo `dist/temp-tools-preview/`.
