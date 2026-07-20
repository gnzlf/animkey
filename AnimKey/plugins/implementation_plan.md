# Optimización del Motion Trail Plugin — AnimKey

## Diagnóstico del Problema

Tras analizar en profundidad los **1154 líneas** de [animKeyTrailPlugin.py](file:///g:/Mi%20unidad/Animacion/personal/ANIMKEY/ANIMKEY/AnimKey/plugins/animKeyTrailPlugin.py) y las **2443 líneas** de [trail.py](file:///g:/Mi%20unidad/Animacion/personal/ANIMKEY/ANIMKEY/AnimKey/buttons/trail.py), he identificado la **causa raíz exacta** del rendimiento lento:

### 🔴 El Problema Central: `prepareForDraw()` recalcula TODO en cada frame

El método [prepareForDraw()](file:///g:/Mi%20unidad/Animacion/personal/ANIMKEY/ANIMKEY/AnimKey/plugins/animKeyTrailPlugin.py#L637-L789) se ejecuta **en cada repintado del viewport** (cada frame de playback, cada movimiento de cámara, cada interacción). Dentro de este método:

```python
# Línea 669-676: BORRA todo el cache en cada llamada
data.points.clear()
data.frames = []
data.key_points.clear()
data.key_frames = []
data.previous_key_frame = None
data.next_key_frame = None
data.pop_frames = set()
data.pop_segments = set()
```

Esto significa que en una animación de 120 frames con increment=1:

| Operación costosa | Por cada repintado |
|---|---|
| `_matrix_position_at_time()` | ~120 evaluaciones de matriz con `MDGContext` |
| `_collect_key_frames_from_source()` | 1 recorrido del grafo de nodos con `cmds.listConnections` |
| `_key_motion_break_score()` | N evaluaciones por cada keyframe para "pop detection" |
| `_motion_break_score()` en samples | ~118 cálculos de score por puntos muestreados |
| `_point_distance()` | ~236 cálculos de distancia para pop detection |

**Comparación con Maya nativo**: El motion trail nativo de Maya **cachea las posiciones** y solo las recalcula cuando detecta un cambio en las animation curves (via callbacks). Tu plugin recalcula **todo** en cada repintado.

### 🔴 Otros problemas identificados

1. **BoundingBox también evalúa matrices** ([L391-L423](file:///g:/Mi%20unidad/Animacion/personal/ANIMKEY/ANIMKEY/AnimKey/plugins/animKeyTrailPlugin.py#L391-L423)): `boundingBox()` itera y evalúa matrices para calcular el bounding box, y esto también se llama frecuentemente.

2. **Dibujo segmento por segmento** ([L569-L608](file:///g:/Mi%20unidad/Animacion/personal/ANIMKEY/ANIMKEY/AnimKey/plugins/animKeyTrailPlugin.py#L569-L608)): El trail se dibuja con un bucle que crea un `MPointArray` de 2 puntos por cada segmento + una llamada a `mesh()` por segmento. Para 120 puntos = ~238 draw calls (halo + color).

3. **`circle2d` con 28 segmentos** por cada key marker ([L557](file:///g:/Mi%20unidad/Animacion/personal/ANIMKEY/ANIMKEY/AnimKey/plugins/animKeyTrailPlugin.py#L557)): Cada keyframe dibuja 2 círculos (outline + fill) con 28 segmentos cada uno.

4. **`setDependentsDirty` no cachea inteligentemente** ([L239-L253](file:///g:/Mi%20unidad/Animacion/personal/ANIMKEY/ANIMKEY/AnimKey/plugins/animKeyTrailPlugin.py#L239-L253)): El sistema de dirty marking existe pero `prepareForDraw` ignora completamente `self.is_dirty` y recalcula todo siempre.

5. **El cache `points_cache` del nodo nunca se usa realmente** ([L233](file:///g:/Mi%20unidad/Animacion/personal/ANIMKEY/ANIMKEY/AnimKey/plugins/animKeyTrailPlugin.py#L233)): `self.points_cache = {}` se declara y se limpia en `setDependentsDirty`, pero `prepareForDraw` (que está en el DrawOverride, NO en el nodo) no accede a él.

---

## ¿Cómo funciona el Motion Trail nativo de Maya?

Maya nativo recalcula posiciones **solo cuando se edita un keyframe** gracias a:
1. Las posiciones se evalúan una vez y se guardan en un buffer interno
2. Un `MNodeMessage::addAttributeChangedCallback` en las animation curves detecta cambios
3. Solo el "current frame marker" se actualiza durante playback (1 sola evaluación de matriz)
4. El bounding box se cachea y solo se recalcula cuando el trail se marca dirty

---

## Propuesta de Cambios

### Fase 1: Cache inteligente en `prepareForDraw()` (IMPACTO CRÍTICO — ~90% de la mejora)

#### [MODIFY] [animKeyTrailPlugin.py](file:///g:/Mi%20unidad/Animacion/personal/ANIMKEY/ANIMKEY/AnimKey/plugins/animKeyTrailPlugin.py)

**Concepto clave**: Separar los datos que cambian **solo cuando hay un cambio en las curvas de animación** de los datos que cambian **cada frame** (solo la posición del current frame y colores/tamaños que dependen del current frame).

##### 1.1. Nuevo sistema de cache en `TrailUserData`

Agregar un **cache persistente** en `TrailUserData` que almacene las posiciones pre-calculadas del trail:

```python
class TrailUserData(om.MUserData):
    def __init__(self):
        super(TrailUserData, self).__init__(False)
        # --- Datos que se recalculan SOLO cuando cambian las curves ---
        self._cached_points = []        # Lista de (frame, MPoint)
        self._cached_key_data = []      # Lista de (frame, MPoint)
        self._cached_pop_frames = set()
        self._cached_pop_segments = set()
        self._cache_hash = None         # Hash para detectar si necesita recalcular
        
        # --- Datos que cambian cada frame (baratos de calcular) ---
        self.points = om.MPointArray()  # Se copia del cache
        self.frames = []
        # ... etc (el resto igual)
```

##### 1.2. Lógica de cache en `prepareForDraw()`

```python
def prepareForDraw(self, objPath, cameraPath, frameContext, oldData):
    # ... leer atributos (barato) ...
    
    # Calcular hash del estado actual
    cache_key = (start_time, end_time, increment, 
                 round(data.pop_threshold, 3),
                 data.show_pop_warnings)
    
    needs_full_recalc = (data._cache_hash != cache_key)
    
    if needs_full_recalc:
        # RECÁLCULO COMPLETO (solo cuando cambia algo en las curves)
        self._rebuild_trail_cache(data, target_plug, ...)
        data._cache_hash = cache_key
    
    # ACTUALIZACIÓN PER-FRAME (barata — solo current position)
    current_pos = _matrix_position_at_time(target_plug, data.current_frame)
    data.current_frame_pos = current_pos or om.MPoint()
    
    # Copiar puntos del cache al MPointArray (sin recalcular)
    data.points.clear()
    data.frames = [item[0] for item in data._cached_points]
    for item in data._cached_points:
        data.points.append(item[1])
    
    # Los colores/tamaños de keys se calculan en el draw (ya lo hacen)
```

> [!IMPORTANT]
> **Esta es la optimización que replica el comportamiento de Maya nativo**: las posiciones del trail se calculan **una sola vez** y se reutilizan. Solo el marcador del frame actual se re-evalúa cada frame.

##### 1.3. Detección de cambios en curvas de animación

El `setDependentsDirty` ya marca `is_dirty` cuando cambia `targetMatrix`. Para que esto funcione correctamente con el DrawOverride (que no tiene acceso directo al nodo):

- Usar el `_cache_hash` como señal: cuando `targetMatrix` cambia, un atributo auxiliar en el nodo se incrementa, y el DrawOverride detecta el cambio comparando su valor anterior.
- Alternativa más limpia: Almacenar los datos cacheados directamente en atributos del nodo que el DrawOverride pueda leer.

**Implementación elegante**: Guardar un "dirty counter" como atributo numérico del nodo. `setDependentsDirty` lo incrementa, y `prepareForDraw` compara el valor actual contra el último valor que cacheo.

##### 1.4. Invalidar cache solo cuando es necesario

```python
def setDependentsDirty(self, plug, plugArray):
    # ... lógica existente ...
    if is_target_plug or time_range_changed:
        self._dirty_counter += 1  # Señal para el DrawOverride
```

---

### Fase 2: Optimización del dibujo (IMPACTO MEDIO — ~5-8% adicional)

##### 2.1. Batch drawing — eliminar el bucle segment-by-segment

**Antes** (actual): ~120 iteraciones con `mesh(kLines, 2_points)` por trail
**Después**: Agrupar segmentos consecutivos del mismo color y dibujarlos en una sola llamada

```python
@staticmethod
def _draw_colored_trail(draw_manager, data):
    count = len(data.points)
    if count < 2:
        return
    
    # Agrupar segmentos por color similar (reducir draw calls)
    current_batch = om.MPointArray()
    current_color = None
    current_width = data.line_width + 1.2
    
    for index in range(count - 1):
        frame_a = data.frames[index]
        frame_b = data.frames[index + 1]
        seg_color = _frame_color(data, (frame_a + frame_b) * 0.5)
        is_pop = _segment_has_pop(data, frame_a, frame_b, index)
        
        if is_pop or seg_color != current_color:
            # Flush batch anterior
            if current_batch and len(current_batch) >= 2:
                draw_manager.setColor(current_color)
                draw_manager.setLineWidth(current_width)
                draw_manager.mesh(omr.MUIDrawManager.kLineStrip, current_batch)
            current_batch = om.MPointArray()
            current_color = seg_color
            current_width = (data.line_width + 4.5) if is_pop else (data.line_width + 1.2)
        
        if len(current_batch) == 0:
            current_batch.append(data.points[index])
        current_batch.append(data.points[index + 1])
    
    # Flush último batch
    if current_batch and len(current_batch) >= 2:
        draw_manager.setColor(current_color)
        draw_manager.setLineWidth(current_width)
        draw_manager.mesh(omr.MUIDrawManager.kLineStrip, current_batch)
```

##### 2.2. Halo como LineStrip

El halo actual es un solo color oscuro. Se puede dibujar como **un solo `kLineStrip`** en vez de N segmentos individuales:

```python
@staticmethod
def _draw_trail_halo(draw_manager, data):
    if len(data.points) < 2:
        return
    draw_manager.setColor(om.MColor((0.01, 0.01, 0.01, 1.0)))
    draw_manager.setLineWidth(data.line_width + 5.0)
    draw_manager.mesh(omr.MUIDrawManager.kLineStrip, data.points)  # Una sola llamada
```

##### 2.3. Reducir segmentos de círculos 2D

Bajar de 28 a 16 segmentos en `circle2d` — visualmente imperceptible para marcadores pequeños:

```python
draw_manager.circle2d(screen_point, radius + 2.0, 16, True)  # era 28
draw_manager.circle2d(screen_point, radius, 16, True)         # era 28
```

---

### Fase 3: Optimización del BoundingBox (IMPACTO BAJO-MEDIO)

##### 3.1. Cachear el bounding box

```python
class TrailUserData(om.MUserData):
    def __init__(self):
        # ...
        self._cached_bbox = None
        self._bbox_hash = None
```

En `boundingBox()`: reutilizar el bbox cacheado del último `prepareForDraw` si el hash coincide. Calcular el bbox durante `prepareForDraw` (cuando ya tenemos los puntos) en vez de evaluar matrices de nuevo.

---

### Fase 4: Mantener handles pero optimizar su overhead

Los handles tangentes (`AnimKeyTangentHandleNode`) son relativamente ligeros ya que:
- Son nodos Maya nativos con draw override simple (1 línea + 1 punto)
- Solo se crean cuando `show_tangent_handles = True`

**No requieren cambios significativos**. Solo se asegura que el callback `_tangent_handle_attr_changed` no dispare recálculos innecesarios del trail principal.

---

## Resumen de Impacto Esperado

| Fase | Cambio | Impacto en FPS | Complejidad |
|------|--------|----------------|-------------|
| **Fase 1** | Cache de posiciones | **~90% mejora** | Media |
| **Fase 2** | Batch drawing | **~5-8% mejora** | Baja |
| **Fase 3** | Cache bbox | **~2-3% mejora** | Baja |
| **Fase 4** | Handles optimization | Mantener actual | Ninguna |

### Lo que se mantiene exactamente igual visualmente:
- ✅ Curva gruesa con gradiente de colores (past/future colors)
- ✅ Colores cambiantes según avanzan los frames (proximity-based color mixing)
- ✅ Keys que se escalan según el frame actual (falloff-based sizing)
- ✅ Círculos 2D con outline oscuro para key markers
- ✅ Current frame marker más grande
- ✅ Pop warnings con color rojo
- ✅ Halo oscuro detrás del trail
- ✅ Tangent handles editables
- ✅ Todas las paletas de colores y personalización

### Lo que cambia internamente:
- ⚡ Las posiciones del trail se evalúan **1 vez** en lugar de **cada frame**
- ⚡ El trail se re-evalúa automáticamente cuando se edita un key (como Maya nativo)
- ⚡ El dibujo del halo pasa de ~120 draw calls a **1 sola llamada**
- ⚡ El bounding box se cachea y no re-evalúa matrices
- ⚡ Los circle2d usan 16 segmentos en vez de 28

---

## Open Questions

> [!IMPORTANT]
> **¿Qué debe disparar una re-evaluación completa?**
> Mi propuesta es: solo cuando `targetMatrix` cambia (que ocurre al editar un key, mover el objeto, cambiar time range, etc). Durante playback puro, solo se actualiza la posición del current frame marker. ¿Esto te parece correcto, o quieres que también se re-evalúe durante playback (pero menos frecuentemente, ej: cada 10 frames)?

> [!NOTE]
> **Sobre los handles**: Los handles tangentes son bastante livianos. Los voy a mantener tal cual porque su overhead es mínimo (cada uno es un nodo con 1 línea + 1 punto). Si en el futuro quieres, se pueden convertir a que se dibujen directamente en el DrawOverride del trail principal (eliminando nodos extra), pero eso es un cambio más grande.

---

## Archivos a Modificar

### [MODIFY] [animKeyTrailPlugin.py](file:///g:/Mi%20unidad/Animacion/personal/ANIMKEY/ANIMKEY/AnimKey/plugins/animKeyTrailPlugin.py)
- Agregar sistema de cache a `TrailUserData` y `AnimKeyMotionTrailNode`
- Reescribir `prepareForDraw()` con lógica de cache inteligente
- Optimizar `_draw_trail_halo()` a una sola llamada `kLineStrip`
- Optimizar `_draw_colored_trail()` con batching de segmentos
- Cachear bounding box
- Reducir segmentos de `circle2d`
- **NO se toca**: `AnimKeyTangentHandleNode`, `AnimKeyTangentHandleDrawOverride`, colores, tamaños de keys

### [MODIFY] [trail.py](file:///g:/Mi%20unidad/Animacion/personal/ANIMKEY/ANIMKEY/AnimKey/buttons/trail.py)
- Modificar `_dirty_custom_trail()` para forzar invalidación del cache cuando se aplica un cambio de apariencia
- Sin cambios en la lógica de creación, handles, paletas, ni UI

## Verification Plan

### Manual Verification
1. Crear trail en un personaje animado con ~100+ frames
2. Dar play a la animación y verificar que el FPS del viewport es similar a sin trail
3. Navegar con la cámara (tumble/pan/zoom) y verificar fluidez
4. Editar un keyframe y verificar que el trail se actualiza inmediatamente
5. Cambiar de paleta de colores y verificar que se aplica correctamente
6. Verificar que los key markers se escalan correctamente según el frame actual
7. Verificar que el current frame marker sigue al objeto durante playback
8. Verificar que los pop warnings siguen apareciendo correctamente
9. Activar tangent handles y verificar que se muestran y funcionan
10. Cambiar time range y verificar que el trail se regenera
