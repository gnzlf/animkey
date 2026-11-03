# Copy/Paste Animation y Pose

Validado el 22 de octubre de 2026 en Maya 2024. Cambios instalados y recargados
en la sesión abierta de Maya.

## Comportamiento

- C copia animación. Su menú contextual contiene Copy/Paste, Paste Insert y
  Save/Load Animation o Pose. Se eliminaron las ventanas de biblioteca y la
  captura de previews GIF.
- Save Animation y Save Pose abren el selector de archivos; el usuario elige
  carpeta y nombre. Guardar no sustituye los clipboards. Load permite reutilizar
  esos archivos y valida sus datos antes de sustituir el clipboard.
- Se asignan controles por identidad sin namespace, incluso con la selección
  de destino en orden inverso. Un control copiado puede pegarse a varios destinos.
- Paste Animation conserva los tiempos originales. Paste Insert comienza en el
  frame actual y sustituye las keys del tramo insertado, conservando las keys
  exteriores. El merge anterior dejaba keys antiguas dentro del movimiento.
- Los rangos de timeline incluyen valores interpolados en sus bordes, incluso
  cuando no contienen ninguna key original. Las tangentes de borde se fijan
  cuando es necesario para que los vecinos del destino no cambien la curva.
- Una copia desde una capa activa captura esa capa; un clip de una sola capa
  se pega en la capa activa de destino. Desde Base Animation se captura el stack;
  los clips de varias capas conservan su distribución por capas.
- El pegado no desbloquea capas existentes ni restaura sobre ellas pesos,
  curvas de peso u otros ajustes del origen. Esto evita modificar otros rigs
  que comparten esas capas.
- Pose captura valores evaluados y crea keys en el frame actual de la capa
  activa. Fuerza una reevaluación en ese mismo frame para mostrar el resultado
  inmediatamente.
- Se corrigieron referencias a funciones inexistentes de Paste en Flash Buttons.

## Referencia de animBot

Se inspeccionaron sin importar ni ejecutar sus módulos los code objects de
`animPoseTransfer310.pyc`, `animTransfer310.pyc` y `poseTransfer310.pyc` instalados.
El informe `dist/copy-transfer-probe.json` conserva rutas, SHA-256 y disassembly.
La ofuscación limita lo que esa inspección permite concluir; no se afirma haber
recuperado su implementación ni se modificaron archivos de animBot.

La [documentación oficial de animBot](https://www.animbot.ca/tooltips/en/)
describe copia entre personajes/namespaces, inserción en el frame actual y pose
evaluada independiente de capas. Se usó como referencia de interacción y se
comprobó el resultado de AnimKey con el evaluador real de Maya.

Paste Animation de AnimKey mantiene las keys fuera del rango copiado; esto
difiere del reemplazo completo documentado para Paste Replace de animBot.

## Verificación

- `dist/copy-paste-focused-tests.log`: **37/37** pruebas aprobadas de copy/paste,
  límites interpolados, tangentes weighted/step, selección Graph Editor,
  asignación entre rigs, capas aditivas/Override, bloqueo, guardado/carga,
  clipboard, menús, Flash Buttons y undo.
- `dist/copy-paste-mirror-regressions.log`: **22/22** pruebas de regresión del
  mirror aprobadas después del cambio en las utilidades compartidas de curvas.
- `dist/copy-paste-sh20-v0030.json`: **24/24** casos aprobados en la escena
  `SQ01-SH20_3D Layout_v0030.ma`. King → Goblin y Goblin → King; Paste Animation,
  Paste Insert desde frames 35 y 55, y pose en frame 37; Base, aditiva y Override.
  Se verificaron keys y muestras entre keys, controles en orden inverso,
  conservación del origen y keys exteriores, frame actual y restauración por undo.
  Controles: FKIndexFinger1_L, FKIndexFinger2_R e IKArm_L, incluido swivel.
- Las modificaciones de prueba se hicieron en procesos desechables. No se
  guardó la escena de producción.
- `dist/copy-paste-installation.json`: SHA-256 de los seis archivos instalados
  coincide con el código validado; contiene la ruta del respaldo previo.
- `dist/copy-paste-reload.json`: menú actualizado y clipboard preservado. La
  sesión principal mantuvo escena, estado modified, selección, frame 60,
  auto-key, capas, undo y cantidad de curvas. No se ejecutaron pastes de prueba
  sobre la sesión principal.

## Alcance y límites

No se afirma paridad universal con animBot ni se probaron otras versiones de
Maya. El recorte conserva la metadata de infinity y evalúa extensiones constant
y linear. No expande ciclos/oscillate fuera del tramo de keys originales.
Los pesos de las capas existentes pertenecen al destino y permanecen iguales;
copiar curvas de capa no garantiza el mismo resultado compuesto si el destino
tiene otro stack o pesos distintos.

La suite general de Offset incluye un error ajeno a esta modificación por
`OffsetSession.resources` inexistente; sus cuatro casos de copia se ejecutaron
y aprobaron por separado. La suite general de menús incluye fallos ajenos de
Link/Worldspace/Mirror; el menú Copy Animation sí fue aprobado.
