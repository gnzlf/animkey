# Motion Trail X-Ray Implementation Plan

## Objetivo

Hacer que el Motion Trail de AnimKey se dibuje siempre por encima de la
geometria en Maya Viewport 2.0.

El resultado esperado es que la curva del trail, los marcadores de keys y los
handles de tangentes permanezcan visibles aunque pasen por detras o por dentro
de una esfera, cubo, rig u otra malla.

## Contexto Observado

En la captura de prueba, los marcadores se ven como cuadrados y la curva del
motion trail queda parcialmente oculta por la esfera. Esto indica dos cosas:

- Los marcadores probablemente se estan dibujando como `kPoints`, lo cual es
  compatible con X-Ray.
- La curva principal se esta dibujando con `kLineStrip`, y esa primitiva no esta
  cubierta por la garantia de X-Ray de `MUIDrawManager`.

Por eso el fix no debe limitarse a mover `beginDrawInXray()`. Tambien hay que
cambiar como se emite la geometria de la curva.

## Update Despues de la Primera Prueba

La primera implementacion con `beginDrawInXray()` correctamente ordenado y la
curva convertida de `kLineStrip` a `kLines` no fue suficiente: los keys si se
dibujaron por encima, pero la curva con gradiente siguio quedando detras de la
geometria.

Eso confirma que el problema es especifico del dibujo de lineas 3D en el
`MPxDrawOverride`. Para imitar mejor el motion trail nativo de Maya, la curva se
debe dibujar como overlay de pantalla:

- Proyectar los puntos world-space del trail a coordenadas de viewport.
- Usar `MFrameContext.kViewProjMtx` como matriz principal, porque los puntos del
  trail ya estan en world-space.
- Dibujar los segmentos con `MUIDrawManager.lineList(points, True)`.
- Mantener fallback a `mesh(kLines)` si la proyeccion o `lineList` no estan
  disponibles.

Este enfoque evita depender del depth buffer para la curva, que es el
comportamiento visual que se busca al compararlo con el motion trail original de
Maya.

## Archivos Involucrados

### Modify

`AnimKey/plugins/animKeyTrailPlugin.py`

Funciones principales:

- `AnimKeyMotionTrailDrawOverride._draw_colored_trail`
- `AnimKeyMotionTrailDrawOverride.addUIDrawables`
- `AnimKeyTangentHandleDrawOverride.addUIDrawables`

Funciones a revisar, pero no necesariamente cambiar:

- `AnimKeyMotionTrailDrawOverride._draw_single_point`
- `AnimKeyMotionTrailDrawOverride._draw_screen_circle`
- `AnimKeyMotionTrailDrawOverride._draw_current_frame_marker`
- `AnimKeyMotionTrailDrawOverride._draw_key_markers`

## Diagnostico Tecnico

### 1. Orden incorrecto de X-Ray

Actualmente el codigo inicia X-Ray antes de iniciar el drawable:

```python
drawManager.beginDrawInXray()
drawManager.beginDrawable(...)
# draw
drawManager.endDrawable()
drawManager.endDrawInXray()
```

El orden correcto para Viewport 2.0 debe ser:

```python
drawManager.beginDrawable(...)
drawManager.beginDrawInXray()
# draw
drawManager.endDrawInXray()
drawManager.endDrawable()
```

Esto debe aplicarse en cada bloque drawable que necesite dibujarse por encima de
la escena.

### 2. `kLineStrip` no queda garantizado por X-Ray

La documentacion de `MUIDrawManager.beginDrawInXray()` indica que el modo X-Ray
afecta drawables creados con `mesh()` solamente cuando la primitiva es una de:

- `MUIDrawManager.kTriangles`
- `MUIDrawManager.kLines`
- `MUIDrawManager.kPoints`

La curva principal del trail usa `MUIDrawManager.kLineStrip`, por lo que puede
seguir siendo depth-tested aunque este dentro de un bloque X-Ray. Ese es el
motivo mas probable de que la curva se vea detras de la esfera.

### 3. `setDepthPriority()` puede interferir o no aportar

El codigo actual intenta usar:

- `MRenderItem.sActiveWireDepthPriority`
- `MRenderItem.sSelectionDepthPriority`

Para este objetivo, esas prioridades no deberian ser necesarias. La correccion
debe apoyarse en X-Ray, que desactiva el depth test para los drawables
compatibles. Mantener prioridades de profundidad dentro del mismo flujo puede
hacer mas dificil aislar el comportamiento.

### 4. Marcadores cuadrados

Aunque hay una funcion llamada `_draw_screen_circle()`, la captura muestra
marcadores cuadrados. Esto es consistente con `kPoints` y `setPointSize()`.

Para este fix se recomienda mantener los marcadores como cuadrados/puntos,
porque `kPoints` si entra en X-Ray. Convertirlos a circulos reales debe quedar
fuera de alcance, salvo que se haga con `kTriangles` tipo billboard.

## Alcance

### Incluido

- Corregir el orden de `beginDrawable()` y `beginDrawInXray()`.
- Dibujar la curva principal usando `kLines` en lugar de `kLineStrip`.
- Dibujar la curva como overlay 2D con `lineList(..., True)` cuando sea posible.
- Mantener los marcadores como `kPoints` o asegurar una ruta compatible con
  X-Ray.
- Eliminar `setDepthPriority()` de los bloques X-Ray.
- Aplicar la misma estructura X-Ray a los handles de tangente.
- Mantener la apariencia actual tanto como sea posible.

### Fuera de alcance

- Redisenar los marcadores como circulos reales.
- Cambiar colores, tamano, paletas o UI.
- Reescribir el sistema de cache.
- Cambiar la logica de creacion del trail.
- Cambiar la interaccion de tangent handles.

## Plan de Implementacion

### Paso 1: Crear helpers pequenos para X-Ray

Agregar helpers internos en `AnimKeyMotionTrailDrawOverride` para reducir
duplicacion y asegurar que todos los bloques cierren correctamente.

Propuesta:

```python
@staticmethod
def _begin_xray(draw_manager):
    try:
        draw_manager.beginDrawInXray()
        return True
    except Exception:
        return False

@staticmethod
def _end_xray(draw_manager, started):
    if not started:
        return
    try:
        draw_manager.endDrawInXray()
    except Exception:
        pass
```

No es obligatorio crear estos helpers si el cambio queda mas claro inline, pero
ayudan a evitar cierres desbalanceados.

### Paso 2: Convertir la curva de `kLineStrip` a `kLines`

Modificar `_draw_colored_trail`.

Estado actual conceptual:

```python
current_batch.append(data.points[index])
current_batch.append(data.points[index + 1])
draw_manager.mesh(omr.MUIDrawManager.kLineStrip, current_batch)
```

Estado objetivo:

```python
current_batch.append(data.points[index])
current_batch.append(data.points[index + 1])
draw_manager.mesh(omr.MUIDrawManager.kLines, current_batch)
```

Para `kLines`, el batch debe contener pares independientes:

```text
p0, p1, p1, p2, p2, p3 ...
```

No debe contener solo:

```text
p0, p1, p2, p3 ...
```

porque `kLines` interpreta cada par como una linea separada.

Pseudoimplementacion:

```python
current_batch = om.MPointArray()

for index in range(count - 1):
    # calcular color y ancho del segmento

    if debe_flush:
        draw_manager.mesh(omr.MUIDrawManager.kLines, current_batch)
        current_batch = om.MPointArray()

    current_batch.append(data.points[index])
    current_batch.append(data.points[index + 1])
```

Mantener la logica existente de:

- colores por pasado/futuro/current frame
- pop warnings
- ancho distinto para segmentos pop
- batching por color/ancho

### Paso 3: Corregir `AnimKeyMotionTrailDrawOverride.addUIDrawables`

Reestructurar el primer bloque, que dibuja la curva:

```python
drawable_started = AnimKeyMotionTrailDrawOverride._begin_non_selectable_drawable(drawManager)
if drawable_started:
    xray_started = False
    try:
        xray_started = AnimKeyMotionTrailDrawOverride._begin_xray(drawManager)
        AnimKeyMotionTrailDrawOverride._draw_colored_trail(drawManager, data, frameContext)
    finally:
        AnimKeyMotionTrailDrawOverride._end_xray(drawManager, xray_started)
        drawManager.endDrawable()
```

Reestructurar el segundo bloque, que dibuja current marker y key markers:

```python
drawable_started = AnimKeyMotionTrailDrawOverride._begin_non_selectable_drawable(drawManager)
if drawable_started:
    xray_started = False
    try:
        xray_started = AnimKeyMotionTrailDrawOverride._begin_xray(drawManager)
        AnimKeyMotionTrailDrawOverride._draw_current_frame_marker(drawManager, data, frameContext)
        AnimKeyMotionTrailDrawOverride._draw_key_markers(drawManager, data, frameContext)
    finally:
        AnimKeyMotionTrailDrawOverride._end_xray(drawManager, xray_started)
        drawManager.endDrawable()
```

Eliminar de este metodo:

- `drawManager.setDepthPriority(omr.MRenderItem.sActiveWireDepthPriority)`
- `drawManager.setDepthPriority(omr.MRenderItem.sSelectionDepthPriority)`
- el `beginDrawInXray()` global antes del primer `beginDrawable()`
- el `endDrawInXray()` global al final del metodo

### Paso 4: Revisar marcadores cuadrados

Verificar el comportamiento de `_draw_screen_circle`.

Si los marcadores actuales se siguen viendo cuadrados y quedan por encima de la
geometria despues de corregir el bloque X-Ray, no hacer mas cambios.

Si algun marcador sigue quedando detras, forzar la ruta compatible con X-Ray:

```python
draw_manager.setColor(color)
draw_manager.setPointSize(radius * 2.0)
AnimKeyMotionTrailDrawOverride._draw_single_point(draw_manager, point)
```

Esto preserva el marcador cuadrado actual y evita depender de `circle2d()`.

Decision recomendada para la primera implementacion:

- Mantener el codigo actual de marcadores.
- Solo cambiarlo si la prueba manual muestra que `circle2d()` se usa y no queda
  por encima.

### Paso 5: Corregir `AnimKeyTangentHandleDrawOverride.addUIDrawables`

Reestructurar el bloque de dibujo de tangentes con el mismo orden:

```python
drawManager.beginDrawable()
xray_started = False
try:
    xray_started = AnimKeyMotionTrailDrawOverride._begin_xray(drawManager)

    drawManager.setLineWidth(1.8)
    drawManager.setColor(data.line_color)
    drawManager.mesh(omr.MUIDrawManager.kLines, line_points)

    drawManager.setPointSize(data.screen_radius)
    drawManager.setColor(data.handle_color)
    drawManager.mesh(omr.MUIDrawManager.kPoints, handle_point)
finally:
    AnimKeyMotionTrailDrawOverride._end_xray(drawManager, xray_started)
    drawManager.endDrawable()
```

Eliminar:

- `beginDrawInXray()` global antes de `beginDrawable()`
- `endDrawInXray()` global al final
- `setDepthPriority(omr.MRenderItem.sSelectionDepthPriority)`

Los handles ya usan `kLines` y `kPoints`, asi que no requieren conversion de
primitiva.

### Paso 6: Mantener excepciones defensivas

El plugin ya usa muchos `try/except` defensivos para evitar romper el viewport.
Mantener esa filosofia, pero evitar que un fallo deje X-Ray o drawable abierto.

Regla:

- Si `beginDrawable()` tuvo exito, siempre intentar `endDrawable()`.
- Si `beginDrawInXray()` tuvo exito, siempre intentar `endDrawInXray()` antes de
  `endDrawable()`.

### Paso 7: No mezclar con optimizaciones

No modificar en este cambio:

- cache del trail
- bounding boxes
- sample density
- detection de pop warnings
- UI de `trail.py`

Esto mantiene el fix pequeno, reversible y facil de probar.

## Riesgos y Mitigaciones

### Riesgo: la curva cambia levemente en uniones

Al pasar de `kLineStrip` a `kLines`, la curva se dibuja como segmentos
independientes. Visualmente deberia ser igual, pero algunas GPUs pueden mostrar
uniones con diferencias minimas.

Mitigacion:

- Mantener el mismo line width.
- Mantener batching por segmentos consecutivos.
- Revisar visualmente curvas con arcos amplios y curvas cerradas.

### Riesgo: orden interno de dibujo en X-Ray

La documentacion indica que varios `mesh()` dentro de X-Ray pueden tener orden
invertido respecto al orden de llamada.

Mitigacion:

- Mantener curva y marcadores en bloques separados.
- Dibujar marcadores despues de la curva para que se lean encima visualmente.

### Riesgo: `circle2d()` no respeta X-Ray

Si `_draw_screen_circle()` usa `circle2d()`, puede no quedar garantizado por
X-Ray.

Mitigacion:

- En primera pasada, probar con el codigo actual.
- Si falla, cambiar marcadores a `kPoints` explicitamente.
- Si se quieren circulos reales mas adelante, implementarlos con triangulos
  billboard (`kTriangles`).

### Riesgo: compatibilidad entre versiones de Maya

`MUIDrawManager.kNonSelectable` puede no estar disponible igual en todas las
versiones, pero el codigo ya tiene fallback en `_begin_non_selectable_drawable`.

Mitigacion:

- Reusar ese helper.
- No introducir APIs nuevas salvo `beginDrawInXray()`/`endDrawInXray()`, que ya
  se usan en el plugin.

## Criterios de Aceptacion

El cambio se considera correcto cuando:

- La curva del motion trail se ve por delante de una esfera que la cruza.
- La curva sigue visible al orbitar la camara en Viewport 2.0.
- Los marcadores cuadrados de keys se ven por delante de la geometria.
- El marcador del current frame se ve por delante de la geometria.
- Los tangent handles, si estan activos, se ven por delante de la geometria.
- No aparecen errores de Python al recargar el plugin.
- No hay cambios visibles no deseados en colores, tamanos o posicion del trail.

## Verification Plan

### Prueba 1: Trail atravesando una esfera

1. Abrir Maya con Viewport 2.0.
2. Recargar el plugin de AnimKey.
3. Crear una esfera en el centro de la escena.
4. Crear o seleccionar un objeto animado cuyo trail pase por detras o por dentro
   de la esfera.
5. Generar el Motion Trail de AnimKey.
6. Orbitar la camara.
7. Confirmar que la curva permanece visible por encima de la esfera.

### Prueba 2: Marcadores

1. Usar una animacion con varios keyframes visibles.
2. Confirmar que los marcadores cuadrados se ven sobre la geometria.
3. Mover el frame actual.
4. Confirmar que el marcador del current frame se mantiene encima.

### Prueba 3: Tangent handles

1. Activar tangent handles si la herramienta lo permite.
2. Ubicar los handles detras o dentro de geometria.
3. Confirmar que la linea y el punto del handle se dibujan por encima.
4. Confirmar que la interaccion sigue funcionando.

### Prueba 4: Regresion visual

1. Revisar un trail sin geometria delante.
2. Confirmar que la curva mantiene continuidad visual.
3. Confirmar que los colores past/future/current siguen igual.
4. Confirmar que los pop warnings siguen usando su color esperado.

## Rollback Plan

Si el cambio produce problemas:

1. Revertir `_draw_colored_trail()` para usar `kLineStrip`.
2. Revertir el orden de X-Ray solo si Maya muestra errores graves de drawable.
3. Restaurar `setDepthPriority()` solo como experimento aislado, no mezclado con
   X-Ray.

Rollback minimo recomendado:

- Mantener la estructura correcta de `beginDrawable()` y X-Ray.
- Revertir solamente la conversion `kLines` si hubiera un problema visual
  inesperado.

## Secuencia Recomendada de Trabajo

1. Implementar conversion de curva a `kLines`.
2. Reordenar X-Ray en el draw override principal.
3. Reordenar X-Ray en el draw override de tangent handles.
4. Ejecutar una revision estatica del archivo para confirmar que no quedan
   `setDepthPriority()` en esos bloques.
5. Probar manualmente en Maya con la escena de esfera.
6. Ajustar marcadores solo si no quedan por encima.

## Notas de Documentacion

Referencia tecnica usada para este plan:

- Autodesk Maya API: `MUIDrawManager.beginDrawInXray()`
- Autodesk Maya API example: `squaresNode_noDepthTest.cpp`

Punto clave de la documentacion:

- X-Ray solo afecta drawables creados con `MUIDrawManager.mesh()` cuando la
  primitiva es `kTriangles`, `kLines` o `kPoints`.
