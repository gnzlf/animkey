# Mirror: comparacion AnimBot / AnimKey

Fecha: 2026-10-22. Estado: correccion manual validada contra los dos King de la v0031 e instalada en Maya. No se afirma paridad universal con todas las funciones de AnimBot.

## Revision Actual: Referencia v0031

La referencia es `SQ01-SH20_3D Layout_v0031.ma`: `King_Rigging` y `King_Rigging1` contienen la animacion opuesta preparada por el usuario con AnimBot. Las pruebas con modificaciones abrieron esa version en Maya standalone aislado, sin guardar el archivo. La v0037 abierta en la sesion principal solo se uso para recargar el codigo; no sustituye esta referencia.

- Corregido el descarte de snapshots precisos por helpers sin atributos animables, que activaba silenciosamente el fallback rapido.
- La seleccion de la raiz del rig ahora resuelve sus controles animables, excluyendo guias de construccion y nodos internos de deformacion.
- El snapshot preciso puede reutilizarse entre namespaces del mismo archivo referenciado, verificando la estructura de controles. No se comparte entre assets distintos.
- Transferencia de curvas agrupada y deteccion de keys por layer sin recorrer individualmente miles de atributos vacios.
- `dist/mirror-v0031-golden-flip.json`: 52 ejecuciones reales de Mirror Pose, 26 frames en ambos sentidos, 452 controles por rig y 4041 canales por caso; cero diferencias en las muestras comparadas. Mirror Animation tambien coincide en las tres layers y undo restaura los keys originales.
- `dist/mirror-v0031-golden-animation-fast.json`: repeticion final de animacion en las tres layers; cero diferencias en los 26 frames evaluados, undo correcto y tiempos de 15.42 / 14.80 segundos por rig. La pasada previa a la optimizacion tardaba aproximadamente 182 segundos por rig. No es una comparacion de rendimiento con AnimBot.
- `dist/mirror-v0031-unit-tests.log`: 30 pruebas aprobadas.

Instalacion final: actualizado solo `Documents/maya/AnimKey/buttons/mirror.py`, respaldo `dist/mirror-v0031-backup-20261022-142952`. Se conservaron los cambios existentes del modulo compartido de transferencia de curvas. `dist/mirror-update-reload.json` confirma la recarga en la v0037 sin cambiar archivo, frame, seleccion ni estado modified. King tiene perfil preciso compatible; King_Rigging1 se valido en la v0031, no esta presente en esa sesion v0037. No se guardo la escena ni se modificaron los archivos de AnimBot. Los pendientes de Auto Mirror descritos abajo siguen fuera de esta validacion manual.

## Historial Anterior: v0030

Las secciones siguientes registran la validacion anterior por coeficientes y pruebas sinteticas. No sustituyen la comparacion real de la v0031 descrita arriba; las afirmaciones sobre perfiles en la sesion anterior no describen necesariamente la sesion actual.

## Instalacion

- Actualizados `Documents/maya/AnimKey/buttons/mirror.py` y `Documents/maya/AnimKey/core/animation_curve_transfer.py`.
- Perfiles de King y Goblin instalados y reconocidos por los controles de la sesion actual.
- Modulos recargados sin cambiar escena, frame, seleccion ni estado modified. No se guardo la escena.
- Respaldo: `dist/mirror-update-backup-20261022-074714`.
- Confirmacion: `dist/mirror-update-reload.json`.

## Alcance Y Evidencia

Maya 2024.2, Python 3.10, AnimBot 2.0.8. Escena final: `SQ01-SH20_3D Layout_v0030.ma`, con referencias de King y Goblin y animation layers. Las pruebas finales con modificaciones se ejecutaron en procesos aislados.

REA/Ghidra inspecciono las bibliotecas originales sin modificarlas:

- `dist/rea-animbot-mayalib0-evidence.json`: getMirrorValueAt, procedimiento `0x1800509e0`, evidencia `ev_8af89d7ca94b35ab93c2fdeb0d5b3e2183a9073c056085a6260622f01a6b092c`.
- `dist/rea-animbot-snapshot-evidence.json`: localizacion y descompilacion del stress test.
- `dist/animbot-mirror-static.json`: inspeccion estatica del flujo Python.
- `dist/mirror-active-animbot-swivel.json`: ejecucion previa del comando normal CORE.mirror.mirrorPose_click() en la sesion inicializada.
- `dist/mirror-native-channel-comparison.json`: evaluador nativo en una copia aislada.

Lo observado: el mirror obtiene el valor opuesto y aplica un multiplicador, recogiendo valores antes de escribir. El evaluador usa contexto temporal: en canales animados puede leer la curva, no el valor transitorio asignado con setAttr. Esto explica los ceros de las piernas en la prueba inicial; no demostraban un filtro de controles inactivos.

## Correcciones

- Calibracion por rotaciones relativas conservando la paridad de matrices reflejadas. El metodo anterior invertia canales de dedos.
- Eliminada la excepcion que copiaba traslaciones de controles bend sin calibrarlas.
- Medicion acotada de nodos downstream y cadenas IK para atributos indirectos como swivel.
- Pose y animacion aplican el mismo mapa de canales, sin resolver nuevamente matrices world-space de los IK.
- Copia directa con signo: conserva vueltas completas de rotacion, escalas y defaults asimetricos, sin recortes heuristicos.
- Se evitan lecturas redundantes del destino en mapas de copia directa.
- Snapshot reutiliza la calibracion guardada del usuario en scripts/animBot_data/mirror_settings_snapshot.anb cuando coinciden namespace, ruta padre/control unica y signos reciprocos del par. No hay excepciones por nombre de personaje.
- El perfil conserva los signos importados y su procedencia/SHA-256. Mirror no necesita AnimBot ni relee su archivo al ejecutar pose o animacion.
- Sin una calibracion compatible, Snapshot mantiene la calibracion geometrica propia. Los perfiles antiguos deben reconstruirse.
- Las curvas temporales se crean mediante comandos undoables y se rellenan mediante API. Undo/redo ya no deja curvas huerfanas al transferir animacion.

## Resultados

`dist/mirror-calibration-focused-tests.log`: 23 pruebas aprobadas, incluyendo seis ordenes Euler, padres con escala negativa, dedos, bend, IK, atributos indirectos, defaults asimetricos, swap reversible, capas, importacion segura de signos y undo/redo sin residuos. Incluye la regresion existente de transferencia batch.

`dist/mirror-sh20-v0030-calibration.json`: passed: true.

| Rig | Coeficientes comparados | Diferencias | Pose | Animacion |
| --- | ---: | ---: | ---: | ---: |
| King | 2916 | 0 | 7/7 | 6/6 |
| Goblin | 2001 | 0 | 7/7 | 6/6 |

King reutilizo 2916 signos y Goblin 1905. Los otros canales comparados de Goblin mantienen sus mapas geometricos; no todos corresponden a pares aplicables al mirror.

Pose: frames 1, 11, 35 y 70, ambos sentidos, swap, rig trasladado/rotado, puno sintetico y swivel no nulo. Animacion: dedos y swivel en Base Animation, layer aditiva y override, rango 11-21, tangentes weighted y evaluacion entre keys. Error maximo de muestras: cero. Los keys fuera del rango se conservaron y el undo restauro los keys originales de la escena aislada.

La comparacion final es contra los snapshots guardados del usuario. No equivale a ejecutar todos los comandos/opciones de AnimBot como referencia en cada caso. Tampoco se afirma una mejora de velocidad medida contra AnimBot. No se modificaron licencias, autenticacion ni los archivos de instalacion de AnimBot.

## Limites Y Pendientes

- El snapshot nuevo de AnimBot falla en standalone al restaurar curvas con kInvalidParameter: Index not in valid range. No se cambio su codigo ni se omitieron controles de autenticacion para forzar la prueba.
- La bateria antigua test_mirror_rig_snapshots presenta tres fallos y dos errores tambien en la instalacion original, incluyendo _rig_scope inexistente y colisiones de namespaces.
- test_mirror_stability presenta tres fallos y un error de Auto Mirror tanto antes como despues del cambio. Evidencias: dist/mirror-stability-baseline.log y dist/mirror-stability-tests.log. Son pendientes del modo automatico, no pruebas aprobadas del mirror manual.
- No se verificaron otras versiones de Maya en esta pasada.

## Estado De La Prueba Inicial En La Sesion Principal

El undo dentro del MEL del command port no estaba disponible; se ejecuto despues mediante maya.utils.executeDeferred. Valores de controles, curvas, tangentes, frame, seleccion y auto-key quedaron restaurados. King conserva los mismos reference edits. Goblin conserva 14 registros adicionales de setAttr de escala y metadatos de los dos IKLeg, sin diferencias de valores escalares. No se descargaron referencias ni se eliminaron edits para intentar limpiar esos registros en una escena sin guardar.

Evidencias: dist/mirror-active-restoration.json y dist/mirror-active-reference-verification.json. Las pruebas posteriores no modificaron la escena principal ni los snapshots originales de AnimBot.
