# Animation Offset: diagnostico y plan de refactorizacion

## 1. Estado del documento

- Proyecto: AnimKey para Autodesk Maya.
- Modulo principal: `AnimKey/buttons/animation_offset.py`.
- Entorno comprobado: Maya 2024 y `mayapy.exe` de Maya 2024.
- Estado actual: implementacion terminada en el motor revision 3; validacion
  automatizada completada en Maya 2024 Standalone.
- Objetivo de este documento: permitir que otro modelo o desarrollador implemente la correccion sin tener que reconstruir el diagnostico.

Este documento contiene codigo de diseno y ejemplos cercanos a la implementacion final. No se debe copiar cada bloque de forma ciega: primero hay que comprobar las firmas disponibles en Maya 2024 y conservar las APIs publicas que ya usa AnimKey.

## 2. Objetivo funcional

Animation Offset debe permitir este flujo:

1. El animador selecciona uno o varios controles.
2. Selecciona un rango en el Time Slider, o usa todo el playback range.
3. Activa Animation Offset.
4. Modifica traslacion, rotacion, escala o un atributo numerico compatible en un frame.
5. La diferencia se propaga una sola vez a las keys del atributo dentro del rango.
6. La key que el usuario edito no recibe el mismo delta una segunda vez.
7. El animador puede navegar con coma y punto de forma inmediata.
8. Cambiar de frame nunca se interpreta como una nueva edicion.
9. Al soltar el control, la pose queda estable y no continua trasladandose o rotando.
10. Undo revierte un gesto continuo en un solo paso.

## 3. Sintomas informados

- Coma y punto tardan en cambiar de key mientras Animation Offset esta activo.
- En ocasiones Maya queda pensando durante varios segundos.
- Un control puede continuar rotando o trasladandose despues de terminar el gesto.
- La pose puede cambiar al navegar entre keys.
- El problema se vuelve mas visible en escenas con muchos controles, keys, atributos o animation layers.

## 4. Entorno y ruta que Maya ejecuta

El repositorio esta en:

```text
C:\Users\gonza\iCloudDrive\Animacion\personal\ANIMKEY\ANIMKEY
```

Maya 2024 no importa ese directorio directamente. El `userSetup.py` agrega el directorio de usuario de Maya a `sys.path`, por lo que la copia ejecutada es:

```text
C:\Users\gonza\Documents\maya\AnimKey
```

Antes y despues de cada despliegue se debe comprobar la ruta real:

```python
import inspect
import AnimKey.buttons.animation_offset as animation_offset

print(inspect.getfile(animation_offset))
```

El archivo `animation_offset.py` instalado coincide logicamente con el archivo del repositorio; su hash es diferente por finales de linea. Sin embargo, se detectaron otros modulos Python instalados que no coinciden con el repositorio. La prueba final debe desplegar el paquete completo, no solamente el archivo de offset.

## 5. Verificaciones ya realizadas

- El repositorio estaba limpio al iniciar el analisis.
- `animation_offset.py` tiene 2421 lineas.
- Contiene aproximadamente 99 llamadas a `maya.cmds`.
- Contiene 70 bloques `except` amplios y 30 `pass` silenciosos.
- Mantiene al menos 27 variables globales relacionadas con la sesion.
- No existe actualmente una suite de tests del proyecto.
- Todo el paquete compila con `mayapy -m compileall`.
- El modulo se puede importar usando Maya Standalone 2024.

## 6. Flujo actual y causas del problema

### 6.1 Polling continuo y cola sin limite

`offset_animation_deferred()` crea un hilo Python que duerme 50 ms y agrega otra llamada mediante `maya.utils.executeDeferred()`.

Problemas:

- No comprueba si la llamada anterior ya fue procesada.
- Si una actualizacion demora mas de 50 ms, la cola crece.
- Las llamadas atrasadas siguen leyendo el estado global de una sesion que ya avanzo.
- El `generation` actual invalida sesiones antiguas, pero no agrupa callbacks de la misma sesion.
- Maya API y `cmds` deben ejecutarse de manera controlada en el hilo principal.

Este es el principal candidato para la sensacion de atraso y para movimientos que parecen continuar despues de soltar el control.

### 6.2 Coma y punto provocan procesamiento repetido

El filtro `_OffsetKeyNavigationFilter` incluye:

```python
ShortcutOverride
KeyPress
KeyRelease
```

En los tres eventos llama a `_flush_offset_before_time_navigation()`. Cuando Maya cambia el frame, el `scriptJob` de `timeChanged` vuelve a llamar a `_flush_previous_frame_change()` y `adjust_keyframes()`.

Una sola pulsacion puede producir varios barridos completos del rango. El filtro tampoco diferencia adecuadamente un evento inicial de eventos repetidos.

### 6.3 Trabajo O(controles x atributos x keys) en cada tick

`adjust_keyframes()` realiza en caliente:

- consulta de seleccion;
- validacion de objetos;
- validacion de cada atributo;
- lectura del valor actual;
- consulta de existencia de key;
- consulta de todas las keys del atributo;
- comparacion con baseline;
- aplicacion por key;
- segunda captura de todos los valores al terminar.

Aunque nada haya cambiado, el polling vuelve a ejecutar gran parte de ese trabajo.

### 6.4 Muestreo de todos los frames al activar

`_sample_evaluated_baseline_layer_aware()` recorre cada frame entero del rango para cada atributo animado. Por ejemplo:

```text
30 controles x 9 atributos x 1000 frames = 270000 evaluaciones
```

Esto ocurre antes de que la herramienta quede activa. No es necesario guardar todos los frames: las keys pueden almacenarse una vez y los frames intermedios pueden evaluarse bajo demanda.

### 6.5 Aplicacion de una llamada por key

`_layer_aware_apply_delta()` recorre las keys y ejecuta `cmds.keyframe()` para cada una. Esta es una frontera lenta entre Python y Maya. El mismo resultado puede aplicarse por rangos de indices con una o dos llamadas por curva.

### 6.6 Navegacion confundida con edicion

`timeChanged` llama a `adjust_keyframes()`. Ese metodo vuelve a calcular:

```python
desired_diff = current_value - original_value
```

Al cambiar de frame, el valor evaluado tambien cambia. Si el snapshot, la capa o una key protegida no coinciden exactamente, esa evaluacion puede parecer una edicion nueva y propagarse. La regla correcta debe ser:

> El delta cambia solo como respuesta a una edicion real del usuario; nunca como respuesta a `timeChanged`.

### 6.7 Captura de cambios incompleta

Existe `_start_offset_attr_jobs()`, pero no hay ninguna llamada que lo inicie. Por lo tanto, `_anim_offset_live_values` no recibe el flujo para el cual fue disenado. Agregar cientos de `scriptJob` tampoco es la solucion: se reemplazara por callbacks de nodo que solamente marquen tracks como dirty.

### 6.8 Cambios de slider acumulables

`apply_slider_offset_changes()` procesa cada cambio como una operacion independiente. Si dos keys del mismo atributo llegan en la misma lista, ambas calculan `delta_to_apply` usando el mismo cache anterior y luego se aplican secuencialmente. Esto puede sumar offsets incompatibles.

Los cambios deben agruparse por plug antes de tocar las curvas.

### 6.9 Apagado en orden incorrecto

Al desactivar, el codigo hace primero:

```python
_anim_offset_run_timer = False
_anim_offset_active = False
_flush_offset_before_time_navigation()
```

Pero el flush retorna inmediatamente cuando la sesion no esta activa. La sesion correcta debe hacer commit primero y limpiar despues.

### 6.10 Estado duplicado y errores silenciosos

`_animation_offset_original_values` y `_anim_offset_frozen_baseline` duplican gran parte de la misma informacion. `_anim_offset_activation_values` se escribe y limpia, pero no participa en el camino activo. `_resnapshot_tracked_objects_as_baseline()` y `_start_offset_attr_jobs()` no tienen call-sites.

Los errores silenciosos pueden dejar abierto un undo chunk o activo un callback sin informar el problema.

## 7. Invariantes de la nueva implementacion

La implementacion se debe considerar incorrecta si rompe cualquiera de estas reglas:

1. Solo puede existir una `OffsetSession` activa.
2. Solo puede existir una actualizacion Qt pendiente.
3. No debe existir ningun hilo Python del offset.
4. Ningun callback de Maya debe editar curvas directamente.
5. `timeChanged` no puede crear ni modificar un delta.
6. Cada track conserva un baseline inmutable durante su participacion en la sesion.
7. Cada track tiene un unico `applied_delta` acumulado.
8. Las curvas reciben solamente `desired_delta - applied_delta`.
9. Una key editada por el usuario queda protegida de la propagacion de ese mismo incremento.
10. Los callbacks provocados por escrituras internas deben ignorarse.
11. Un `stop()` repetido debe ser seguro.
12. Todo callback, timer y undo chunk creado por `start()` debe eliminarse en `stop()`.
13. Si un nodo desaparece, su track se descarta sin detener Maya.
14. No se propagan valores `NaN`, infinitos, booleanos, enums o atributos no editables.

## 8. Division propuesta de archivos

Para reducir el riesgo, se conserva la API publica del boton y se separa el motor:

```text
AnimKey/buttons/animation_offset.py
    API publica execute(), is_active(), get_info(), boton y overlay.

AnimKey/core/animation_offset_session.py
    OffsetSession, callbacks, scheduler, tracks, capas, undo y commit.

AnimKey/core/animation_offset_math.py
    Funciones puras: tolerancias, rotaciones, rangos e indices.

tests/test_animation_offset_math.py
    Tests sin UI.

tests/maya/test_animation_offset_session.py
    Tests de integracion con Maya Standalone.
```

Las funciones que ya sean importadas externamente deben conservar un wrapper de compatibilidad en `animation_offset.py`.

## 9. Modelo de estado propuesto

### 9.1 Funciones matematicas puras

```python
# AnimKey/core/animation_offset_math.py

import math

FRAME_EPSILON = 1e-4
VALUE_EPSILON = 1e-8


def is_finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def frames_equal(left, right, epsilon=FRAME_EPSILON):
    return abs(float(left) - float(right)) <= epsilon


def shortest_angle_step(previous, current):
    """Return the nearest signed angular step in degrees."""
    step = float(current) - float(previous)
    while step > 180.0:
        step -= 360.0
    while step < -180.0:
        step += 360.0
    return step


def contiguous_index_runs(indices):
    """Convert [0, 1, 2, 5, 6] into [(0, 2), (5, 6)]."""
    ordered = sorted(set(int(index) for index in indices))
    if not ordered:
        return []

    runs = []
    start = previous = ordered[0]
    for index in ordered[1:]:
        if index != previous + 1:
            runs.append((start, previous))
            start = index
        previous = index
    runs.append((start, previous))
    return runs
```

### 9.2 Track por atributo

```python
# AnimKey/core/animation_offset_session.py

from dataclasses import dataclass, field


@dataclass
class TrackState:
    track_id: str
    node_uuid: str
    node_path: str
    attr: str
    attr_type: str
    curve: str | None
    target_layer: str | None
    key_times: tuple[float, ...]
    key_values: dict[float, float]
    evaluated_baselines: dict[float, float] = field(default_factory=dict)
    applied_delta: float = 0.0
    dirty: bool = False
    dirty_time: float | None = None
    last_observed_value: float | None = None
    rotation_accumulator: float = 0.0

    @property
    def plug(self):
        return "{}.{}".format(self.node_path, self.attr)

    @property
    def is_rotation(self):
        return self.attr in {"rotateX", "rotateY", "rotateZ"}
```

Si la sintaxis `str | None` no es compatible con la version minima real de Python soportada, usar `Optional[str]`. Maya 2024 si permite la sintaxis moderna, pero el requisito de versiones de AnimKey debe decidirse antes de implementarla.

### 9.3 Sesion unica

```python
from enum import Enum


class SessionPhase(Enum):
    INACTIVE = "inactive"
    STARTING = "starting"
    ACTIVE = "active"
    COMMITTING = "committing"
    STOPPING = "stopping"


class OffsetSession(QtCore.QObject):
    def __init__(self, parent=None):
        super(OffsetSession, self).__init__(parent)
        self.phase = SessionPhase.INACTIVE
        self.time_range = None
        self.target_layer = None
        self.current_time = None
        self.tracks = {}
        self.dirty_track_ids = set()
        self.callback_ids = []
        self.script_jobs = []
        self.internal_edit_depth = 0
        self.undo_chunk_open = False
        self.generation = 0

        self.flush_timer = QtCore.QTimer(self)
        self.flush_timer.setSingleShot(True)
        self.flush_timer.setInterval(0)
        self.flush_timer.timeout.connect(self._flush_scheduled)

        self.undo_close_timer = QtCore.QTimer(self)
        self.undo_close_timer.setSingleShot(True)
        self.undo_close_timer.setInterval(150)
        self.undo_close_timer.timeout.connect(self.close_undo_chunk)
```

## 10. Ciclo de vida

### 10.1 Activacion

```python
def start(self, objects, time_range, target_layer):
    if self.phase is not SessionPhase.INACTIVE:
        self.stop(commit=True)

    self.phase = SessionPhase.STARTING
    self.generation += 1

    try:
        self.time_range = normalize_range(time_range)
        self.target_layer = target_layer
        self.current_time = float(cmds.currentTime(query=True))

        for node in objects:
            self._register_object(node)

        if not self.tracks:
            raise RuntimeError("No compatible animated attributes in range")

        self._install_time_callback()
        self._install_selection_callback()
        self._install_navigation_filter()
        self.phase = SessionPhase.ACTIVE
    except Exception:
        self._cleanup_runtime_state()
        self.phase = SessionPhase.INACTIVE
        raise
```

La activacion debe ser O(numero de tracks + numero de keys), nunca O(numero de tracks x numero de frames).

### 10.2 Desactivacion

```python
def stop(self, commit=True):
    if self.phase is SessionPhase.INACTIVE:
        return
    if self.phase is SessionPhase.STOPPING:
        return

    self.phase = SessionPhase.STOPPING
    self.generation += 1

    try:
        if commit:
            self.commit_dirty(reason="stop", allow_while_stopping=True)
    finally:
        self.flush_timer.stop()
        self.undo_close_timer.stop()
        self.close_undo_chunk()
        self._remove_callbacks()
        self._remove_script_jobs()
        self._remove_navigation_filter()
        self._cleanup_runtime_state()
        self.phase = SessionPhase.INACTIVE
```

El commit ocurre antes de desactivar los guardas que necesita.

## 11. Captura dirigida por eventos

Se recomienda un callback de Maya API por nodo, no un `scriptJob` por atributo. El callback debe filtrar los plugs trackeados y solamente marcar estado.

```python
import maya.api.OpenMaya as om


def _install_node_callback(self, node_path):
    selection = om.MSelectionList()
    selection.add(node_path)
    node_object = selection.getDependNode(0)

    callback_id = om.MNodeMessage.addAttributeChangedCallback(
        node_object,
        self._on_attribute_changed,
    )
    self.callback_ids.append(callback_id)


def _on_attribute_changed(self, message, plug, other_plug, client_data=None):
    if self.phase is not SessionPhase.ACTIVE:
        return
    if self.internal_edit_depth:
        return
    if not (message & om.MNodeMessage.kAttributeSet):
        return

    track_id = self._track_id_from_plug(plug)
    if track_id is None:
        return

    track = self.tracks.get(track_id)
    if track is None:
        return

    track.dirty = True
    track.dirty_time = self.current_time
    self.dirty_track_ids.add(track_id)
    self.schedule_flush()
```

No se debe ejecutar `cmds.keyframe`, `cmds.getAttr` ni una busqueda del dependency graph dentro del callback.

## 12. Scheduler coalescente

```python
def schedule_flush(self):
    if self.phase is not SessionPhase.ACTIVE:
        return
    if not self.flush_timer.isActive():
        self.flush_timer.start()


def _flush_scheduled(self):
    generation = self.generation
    if self.phase is not SessionPhase.ACTIVE:
        return

    self.commit_dirty(reason="scheduled")

    if generation != self.generation:
        return
    if self.dirty_track_ids and not self.flush_timer.isActive():
        self.flush_timer.start()
```

Propiedades requeridas:

- Si llegan 100 eventos antes del siguiente ciclo Qt, existe un solo flush.
- Un flush toma una copia del conjunto dirty y lo limpia antes de trabajar.
- Si llegan cambios durante el flush, quedan para una segunda ejecucion.
- Nunca hay un bucle que ejecute mientras el usuario esta inactivo.

## 13. Construccion y cache de tracks

Por cada atributo compatible, la activacion debe resolver una vez:

- UUID y DAG path del objeto;
- nombre largo del atributo;
- tipo de atributo;
- animCurve de la capa objetivo;
- tiempos de keys en el rango;
- valores originales de esas keys;
- indices de las keys dentro de la animCurve.

```python
def _build_track(self, node, attr):
    plug = "{}.{}".format(node, attr)
    if not self._is_supported_plug(plug):
        return None

    curve = resolve_target_curve(plug, self.target_layer)
    if not curve:
        return None

    times = cmds.keyframe(
        curve,
        query=True,
        time=self.time_range,
        timeChange=True,
    ) or []
    values = cmds.keyframe(
        curve,
        query=True,
        time=self.time_range,
        valueChange=True,
    ) or []

    if not times or len(times) != len(values):
        return None

    return TrackState(
        track_id=make_track_id(node, attr),
        node_uuid=query_uuid(node),
        node_path=node,
        attr=attr,
        attr_type=cmds.getAttr(plug, type=True),
        curve=curve,
        target_layer=self.target_layer,
        key_times=tuple(float(value) for value in times),
        key_values={
            float(frame): float(value)
            for frame, value in zip(times, values)
        },
    )
```

No volver a consultar todos los tiempos en cada commit. Se invalida un track solamente cuando:

- el slider crea o elimina una key;
- undo/redo cambia la curva;
- la curva es reemplazada;
- el nodo es renombrado, eliminado o deja de existir;
- se agrega un control nuevo a la sesion.

## 14. Baseline bajo demanda

Para una key existente, el baseline viene de `key_values`. Para un frame sin key, se evalua una sola vez y se guarda:

```python
def baseline_at(self, track, frame):
    keyed = find_value_at_time(track.key_values, frame)
    if keyed is not None:
        return keyed

    cached = find_value_at_time(track.evaluated_baselines, frame)
    if cached is not None:
        return cached

    current_curve_value = evaluate_curve(track.curve, frame)
    baseline = float(current_curve_value) - float(track.applied_delta)
    track.evaluated_baselines[float(frame)] = baseline
    return baseline
```

El baseline nunca se reemplaza por el resultado ya offseteado. Eso evita drift acumulativo.

## 15. Calculo y commit del delta

```python
from contextlib import contextmanager


@contextmanager
def internal_edit(self):
    self.internal_edit_depth += 1
    try:
        yield
    finally:
        self.internal_edit_depth -= 1


def commit_dirty(self, reason, allow_while_stopping=False):
    valid_phase = self.phase is SessionPhase.ACTIVE
    if allow_while_stopping:
        valid_phase = valid_phase or self.phase is SessionPhase.STOPPING
    if not valid_phase or not self.dirty_track_ids:
        return False

    dirty_ids = set(self.dirty_track_ids)
    self.dirty_track_ids.difference_update(dirty_ids)

    self.phase = SessionPhase.COMMITTING
    applied_any = False
    try:
        self.open_undo_chunk()
        with self.internal_edit():
            for track_id in dirty_ids:
                track = self.tracks.get(track_id)
                if track and self._commit_track(track):
                    applied_any = True
    finally:
        for track_id in dirty_ids:
            track = self.tracks.get(track_id)
            if track:
                track.dirty = False
        if self.phase is SessionPhase.COMMITTING:
            self.phase = SessionPhase.ACTIVE

    if applied_any:
        self.undo_close_timer.start()
    return applied_any
```

El estado necesita distinguir `COMMITTING` de `STOPPING`. En la implementacion final puede ser mas simple usar flags separados si el enum hace dificil el `finally`, pero debe existir una transicion verificable.

### 15.1 Commit de un track lineal

```python
def _commit_linear_track(self, track, observed_value, edit_time):
    baseline = self.baseline_at(track, edit_time)
    desired_delta = float(observed_value) - float(baseline)
    incremental_delta = desired_delta - float(track.applied_delta)

    if abs(incremental_delta) <= VALUE_EPSILON:
        return False

    protected_times = [edit_time] if self._has_key(track, edit_time) else []
    self._apply_batched_delta(track, incremental_delta, protected_times)
    track.applied_delta = desired_delta
    track.last_observed_value = float(observed_value)
    return True
```

Si el frame no tiene key, no se protege ningun indice. El delta se aplica a toda la curva y el valor evaluado en ese frame debe terminar coincidiendo con la edicion del usuario.

### 15.2 Continuidad de rotaciones

Una rotacion no puede compararse siempre como un numero lineal porque 10 y 370 grados pueden representar la misma orientacion. El track debe acumular pasos cercanos entre muestras:

```python
def _commit_rotation_track(self, track, observed_value, edit_time):
    if track.last_observed_value is None:
        expected = self.baseline_at(track, edit_time) + track.applied_delta
        track.last_observed_value = expected

    step = shortest_angle_step(track.last_observed_value, observed_value)
    if abs(step) <= VALUE_EPSILON:
        track.last_observed_value = float(observed_value)
        return False

    desired_delta = track.applied_delta + step
    incremental_delta = desired_delta - track.applied_delta
    protected_times = [edit_time] if self._has_key(track, edit_time) else []

    self._apply_batched_delta(track, incremental_delta, protected_times)
    track.applied_delta = desired_delta
    track.last_observed_value = float(observed_value)
    return True
```

Esto debe validarse con giros intencionales mayores a 180 y 360 grados. Si Maya entrega valores continuos durante el gesto, no se debe limitar una rotacion legitima; la normalizacion solo corrige discontinuidades de representacion.

## 16. Aplicacion por lotes

```python
def _apply_batched_delta(self, track, delta, protected_times):
    if not is_finite_number(delta):
        raise ValueError("Non-finite Animation Offset delta")

    editable_indices = []
    for index, frame in enumerate(track.key_times):
        if any(frames_equal(frame, protected) for protected in protected_times):
            continue
        editable_indices.append(index)

    for first_index, last_index in contiguous_index_runs(editable_indices):
        cmds.keyframe(
            track.curve,
            edit=True,
            index=(first_index, last_index),
            relative=True,
            valueChange=float(delta),
        )
```

Para una key protegida en medio de la curva se esperan como maximo dos llamadas: indices anteriores e indices posteriores. Hay que confirmar que los indices cacheados correspondan a la curva completa, no solamente al subconjunto del rango. Si se consultan keys de toda la curva, se debe guardar un mapa explicito entre tiempo e indice real.

## 17. Navegacion con coma y punto

El filtro debe observar un solo tipo de evento y no consumir la tecla:

```python
class OffsetNavigationFilter(QtCore.QObject):
    NAVIGATION_KEYS = {
        qt_key("Key_Comma"),
        qt_key("Key_Period"),
        qt_key("Key_Less"),
        qt_key("Key_Greater"),
    }

    def __init__(self, session, parent=None):
        super(OffsetNavigationFilter, self).__init__(parent)
        self.session = session

    def eventFilter(self, watched, event):
        if event.type() != qt_event_type("KeyPress"):
            return False

        if event.key() not in self.NAVIGATION_KEYS:
            return False

        # O(1) cuando no hay tracks dirty.
        self.session.commit_dirty(reason="key_navigation")
        self.session.close_undo_chunk()
        return False
```

No usar `ShortcutOverride` ni `KeyRelease` para el flush. Los `KeyPress` producidos por auto-repeat pueden llamar a `commit_dirty()`, pero cuando no hay cambios esa llamada debe retornar de inmediato.

### 17.1 `timeChanged`

```python
def on_time_changed(self):
    if self.internal_edit_depth:
        return
    if self.phase is not SessionPhase.ACTIVE:
        return

    # Para scrub o clicks de timeline, los tracks dirty conservan dirty_time.
    self.commit_dirty(reason="time_changed")
    self.current_time = float(cmds.currentTime(query=True))
    self._reset_observation_anchors_for_time(self.current_time)
```

`commit_dirty()` debe usar `track.dirty_time`, no asumir que la edicion ocurrio en el nuevo `currentTime`. Despues se actualizan anchors. No se recorre ninguna curva cuando no hay tracks dirty.

## 18. SelectionChanged

La conducta actual permite que controles seleccionados despues de activar se unan a la sesion. Se mantiene esa capacidad, pero sin revisar la seleccion 20 veces por segundo:

```python
def on_selection_changed(self):
    if self.phase is not SessionPhase.ACTIVE:
        return

    for node in selected_long_paths():
        if not self._has_registered_node(node):
            self._register_object(node)
```

Reglas:

- Un control nuevo recibe su propio baseline en el momento de ingreso.
- Cambiar seleccion no elimina tracks anteriores de la sesion.
- Un nodo eliminado se elimina de `tracks` y sus callbacks se liberan.
- Nombres duplicados se resuelven mediante DAG path largo y UUID.

## 19. Atributos compatibles

La validacion debe ocurrir al crear el track, no en cada tick.

Aceptar inicialmente:

- `double`, `doubleLinear`, `doubleAngle`, `float`;
- translate, rotate y scale desbloqueados;
- atributos custom escalares flotantes editables;
- atributos con una animCurve resoluble en la capa objetivo.

Excluir:

- `bool`, visibility y enums;
- string, message, matrix y compounds;
- atributos bloqueados;
- atributos no settable o manejados por una conexion que no sea la curva objetivo;
- valores no escalares;
- curvas fuera de la capa congelada.

```python
SUPPORTED_TYPES = {
    "double",
    "doubleLinear",
    "doubleAngle",
    "float",
}


def is_supported_plug(plug):
    if not cmds.objExists(plug):
        return False
    if cmds.getAttr(plug, lock=True):
        return False
    if not cmds.getAttr(plug, settable=True):
        return False
    return cmds.getAttr(plug, type=True) in SUPPORTED_TYPES
```

La comprobacion `settable=True` debe probarse con animCurves y animation layers, porque Maya puede considerar no-settable un plug conectado aunque sea animable. Si ocurre, reemplazar esa condicion por una validacion explicita de la cadena de conexiones.

## 20. Animation Layers

La capa activa se resuelve una vez al iniciar y queda congelada:

```python
self.target_layer = get_selected_animation_layer()
```

Cada track debe apuntar a la animCurve perteneciente a esa capa. No usar el valor compuesto del control para calcular un delta de curva si Maya ya creo/modifico una key en la capa: leer la contribucion de la curva objetivo.

Casos obligatorios:

- escena sin layers;
- BaseAnimation;
- layer additive;
- layer override;
- weight distinto de 1;
- layer muteada;
- layer solo;
- cambio de seleccion de layer durante una sesion;
- creacion de la primera key de un atributo en la layer.

Si el animador cambia la layer seleccionada durante una sesion, el target no cambia. La UI debe requerir apagar y volver a activar para elegir otra layer.

## 21. Integracion con sliders

La API publica puede conservarse:

```python
def apply_slider_offset_changes(changes):
    session = get_active_session()
    if session is None:
        return False
    return session.apply_slider_changes(changes)
```

Los cambios se agrupan antes de aplicar:

```python
from collections import defaultdict


def group_slider_changes(changes):
    grouped = defaultdict(list)
    for change in changes:
        plug = change.get("attr_full")
        if not plug:
            continue
        grouped[plug].append(change)
    return grouped
```

Politica por plug:

1. Calcular `new_value - original_value` de todas las keys recibidas.
2. Si todas coinciden dentro de tolerancia, aplicar una sola propagacion y proteger todas esas keys.
3. Si difieren, usar como driver la key del tiempo actual, si esta presente.
4. Si no existe key del tiempo actual, elegir la mas cercana de manera determinista.
5. Conservar las otras keys editadas como protegidas y actualizar su baseline local para no reinterpretarlas en el siguiente callback.
6. Registrar el conflicto en modo debug; nunca aplicar N deltas globales consecutivos al mismo atributo.

El codigo actual de `tweener.py` y `_sync_animation_offset_after_slider()` en `toolbar.py` debe revisarse para asegurar que no existan dos rutas que propaguen el mismo cambio.

## 22. Undo y redo

Objetivo: un gesto continuo equivale a un paso de undo.

```python
def open_undo_chunk(self):
    if self.undo_chunk_open:
        return
    cmds.undoInfo(openChunk=True, chunkName="AnimKey_AnimOffset")
    self.undo_chunk_open = True


def close_undo_chunk(self):
    if not self.undo_chunk_open:
        return
    try:
        cmds.undoInfo(closeChunk=True)
    finally:
        self.undo_chunk_open = False
```

Cerrar el chunk en:

- mouse release;
- navegacion de frame;
- debounce sin cambios;
- desactivacion;
- undo/redo;
- apertura o creacion de escena;
- excepcion durante commit.

Durante undo/redo:

1. Incrementar `internal_edit_depth` o activar un guard dedicado.
2. Cancelar el timer pendiente.
3. Cerrar cualquier chunk.
4. Permitir que Maya termine undo/redo.
5. Invalidar las curvas o keys afectadas.
6. Reconstruir los tracks desde la escena en un deferred unico.
7. Reiniciar anchors sin generar un nuevo offset.

## 23. Overlay y Time Slider

El overlay debe ser solamente visual.

- Crear al activar.
- Actualizar al cambiar rango o tamano del widget.
- Eliminar al desactivar.
- No llamar `raise_()` y `update()` en cada `timeChanged`.
- No escribir `rangeArray` en cada frame.
- Limpiar la seleccion azul una vez al activar, si sigue siendo necesario para conservar la navegacion nativa.

Esto elimina refrescos de UI que actualmente se mezclan con el camino de calculo.

## 24. Manejo de errores y diagnostico

No se deben conservar `except: pass` en fronteras importantes. Usar logging con contexto:

```python
import logging

LOGGER = logging.getLogger("AnimKey.AnimationOffset")


def safe_remove_callback(callback_id):
    try:
        om.MMessage.removeCallback(callback_id)
    except RuntimeError:
        LOGGER.debug(
            "Callback already removed: %s",
            callback_id,
            exc_info=True,
        )
```

En modo normal:

- una advertencia de usuario para errores recuperables;
- un mensaje de error para una sesion abortada;
- no imprimir por cada tick.

En modo debug, agregar un snapshot:

```python
def debug_snapshot(self):
    return {
        "phase": self.phase.value,
        "generation": self.generation,
        "track_count": len(self.tracks),
        "dirty_count": len(self.dirty_track_ids),
        "flush_pending": self.flush_timer.isActive(),
        "callback_count": len(self.callback_ids),
        "undo_chunk_open": self.undo_chunk_open,
        "time_range": self.time_range,
        "target_layer": self.target_layer,
    }
```

## 25. Instrumentacion de rendimiento

Agregar medicion opcional, desactivada por defecto:

```python
import time


class PerfSample:
    def __init__(self, label, sink):
        self.label = label
        self.sink = sink
        self.started = None

    def __enter__(self):
        self.started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        elapsed_ms = (time.perf_counter() - self.started) * 1000.0
        self.sink(self.label, elapsed_ms)
```

Medir:

- construccion de tracks;
- commit por numero de tracks dirty;
- aplicacion por numero de curvas y keys;
- navegacion sin dirty tracks;
- limpieza de sesion;
- cantidad maxima de actualizaciones pendientes.

La navegacion sin cambios debe ser O(1) y no consultar keys.

## 26. Tests unitarios

Usar `unittest`, disponible dentro de `mayapy`, para no agregar una dependencia obligatoria.

```python
# tests/test_animation_offset_math.py

import unittest

from AnimKey.core.animation_offset_math import (
    contiguous_index_runs,
    is_finite_number,
    shortest_angle_step,
)


class AnimationOffsetMathTests(unittest.TestCase):
    def test_contiguous_runs(self):
        self.assertEqual(
            contiguous_index_runs([0, 1, 2, 5, 6, 9]),
            [(0, 2), (5, 6), (9, 9)],
        )

    def test_shortest_positive_wrap(self):
        self.assertAlmostEqual(shortest_angle_step(179.0, -179.0), 2.0)

    def test_shortest_negative_wrap(self):
        self.assertAlmostEqual(shortest_angle_step(-179.0, 179.0), -2.0)

    def test_rejects_non_finite_values(self):
        self.assertFalse(is_finite_number(float("nan")))
        self.assertFalse(is_finite_number(float("inf")))
        self.assertFalse(is_finite_number(True))


if __name__ == "__main__":
    unittest.main()
```

Agregar tests para:

- comparacion de frames con tolerancia;
- proteccion de keys;
- calculo incremental;
- agrupacion de sliders;
- seleccion determinista de driver;
- entradas vacias;
- rangos negativos y subframes;
- rotaciones acumuladas mayores a 360 grados.

## 27. Tests Maya Standalone

El motor debe ofrecer puntos de entrada internos testeables, por ejemplo `start_for_test()`, `flush_now()` y `debug_snapshot()`. No deben formar parte de la UI.

```python
# tests/maya/test_animation_offset_session.py

import unittest

import maya.cmds as cmds

from AnimKey.core.animation_offset_session import OffsetSession


class AnimationOffsetSessionTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.control = cmds.createNode("transform", name="offset_ctrl")
        for frame, value in ((1, 0.0), (10, 5.0), (20, 10.0)):
            cmds.setKeyframe(
                self.control,
                attribute="translateX",
                time=frame,
                value=value,
            )

    def tearDown(self):
        active = OffsetSession.active_instance()
        if active is not None:
            active.stop(commit=False)

    def test_translation_is_applied_once(self):
        session = OffsetSession()
        session.start([self.control], (1.0, 20.0), target_layer=None)

        cmds.currentTime(10.0)
        cmds.setKeyframe(
            self.control,
            attribute="translateX",
            time=10.0,
            value=7.0,
        )
        session.mark_dirty_for_test(self.control, "translateX", 10.0)
        session.flush_now()

        values = cmds.keyframe(
            self.control,
            attribute="translateX",
            query=True,
            valueChange=True,
        )
        self.assertEqual(values, [2.0, 7.0, 12.0])

        session.flush_now()
        values_again = cmds.keyframe(
            self.control,
            attribute="translateX",
            query=True,
            valueChange=True,
        )
        self.assertEqual(values_again, values)
```

Otros tests de integracion:

1. Aplicar translate y ejecutar 100 flush adicionales: no cambia nada.
2. Aplicar rotate cruzando 180/-180: no aparece un delta de 358 grados.
3. Cambiar `currentTime` 200 veces: las curvas no cambian.
4. Activar/desactivar 100 veces: callback count vuelve a cero.
5. Eliminar el control durante la sesion: no quedan callbacks.
6. Agregar control mediante SelectionChanged: obtiene baseline propio.
7. Undo y redo: curvas, cache y estado coinciden.
8. Keys stepped conservan sus tangentes.
9. Rigs con nombres cortos duplicados actualizan el DAG path correcto.
10. Rango seleccionado modifica solamente sus keys.

### 27.1 Runner con `mayapy`

```powershell
& 'C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe' `
  -m unittest discover -s . -p 'test_animation_offset*.py' -v
```

Los tests que necesitan widgets Qt o el Time Slider real deben ejecutarse dentro de Maya UI, no en Standalone.

## 28. Matriz de prueba manual en Maya UI

| Caso | Variantes | Resultado esperado |
| --- | --- | --- |
| Translate | X/Y/Z, key actual y frame sin key | Delta unico y pose estable |
| Rotate | X/Y/Z, ordenes Euler, cruce 180, giro >360 | Sin salto ni rotacion infinita |
| Scale | uniforme y por eje | Incremento consistente |
| Navegacion | coma, punto, auto-repeat, click y scrub | Cambio inmediato, sin pose flicker |
| Rango | corto, 1000 frames, negativo, subframes | Solo keys del rango |
| Seleccion | uno, muchos, cambiar control activo | Registro correcto sin barrido continuo |
| Capas | Base, additive, override, weight, mute, solo | Solo cambia la capa congelada |
| Undo | por gesto, repetido, undo/redo | Un paso por gesto y estado coherente |
| Slider | Tweener, varias keys, GE selection | Sin doble propagacion |
| Ciclo de vida | apagar, escena nueva, nodo eliminado | Cero timers y callbacks huerfanos |
| Rigs | references y nombres duplicados | Solo el control correcto |
| UI | toolbar reconstruida y hotkey OFF | Boton refleja estado real |

## 29. Escenas de rendimiento

Crear tres fixtures:

### Escena pequena

- 1 control.
- 9 atributos transform.
- 20 keys por atributo.

### Escena media

- 20 controles.
- 9 atributos.
- 200 keys por atributo.
- 2 animation layers.

### Escena pesada

- 100 controles.
- 9 atributos.
- 1000 keys por atributo.
- 4 animation layers.

Metas:

- Activacion proporcional a tracks + keys, sin muestreo por frame.
- Coma/punto sin dirty tracks no realiza consultas de keys.
- Un gesto con un atributo dirty no escanea atributos no modificados.
- Nunca hay mas de un flush pendiente.
- Al soltar, no existe trabajo periodico residual.

Los umbrales exactos en milisegundos se deben fijar despues de medir la escena real del usuario. Como objetivo inicial, una navegacion sin cambios debe anadir menos de un frame de UI y un commit comun debe sentirse interactivo.

## 30. Orden de implementacion

### Fase 1: tests de caracterizacion

- Crear fixtures y helpers de Maya Standalone.
- Capturar el comportamiento correcto que ya existe.
- Reproducir acumulacion, navegacion y pose drift.
- Agregar mediciones al codigo actual sin cambiar semantica.

### Fase 2: nucleo puro

- Crear `animation_offset_math.py`.
- Implementar tolerancias, rangos, runs y continuidad angular.
- Completar tests unitarios.

### Fase 3: sesion y tracks

- Crear `OffsetSession`.
- Mover resolucion de layer/curve con wrappers compatibles.
- Construir caches sin muestreo por frame.
- Implementar start/stop idempotentes.

### Fase 4: scheduler y callbacks

- Eliminar thread y polling.
- Instalar callbacks por nodo.
- Implementar dirty set y QTimer coalescente.
- Comprobar que las escrituras internas no se realimentan.

### Fase 5: aplicacion y navegacion

- Implementar deltas incrementales y batching.
- Implementar proteccion de driver key.
- Cambiar filtro de coma/punto.
- Reducir `timeChanged` a commit dirty + cambio de anchor.

### Fase 6: integraciones

- Animation layers.
- Sliders y toolbar.
- SelectionChanged.
- Undo/redo.
- Overlay.

### Fase 7: limpieza

- Eliminar globals, funciones muertas y polling.
- Sustituir excepciones silenciosas.
- Mantener wrappers publicos.
- Agregar documentacion de debug.

### Fase 8: validacion y despliegue

- Ejecutar tests unitarios y Standalone.
- Ejecutar matriz UI en Maya 2024.
- Perfilar escenas pequena/media/pesada.
- Copiar el paquete validado a la carpeta de Maya.
- Reiniciar Maya y verificar ruta/version.
- Commit y push solamente despues de la prueba final.

## 31. Archivos que probablemente cambiaran

Cambios principales:

```text
AnimKey/buttons/animation_offset.py
AnimKey/core/animation_offset_session.py
AnimKey/core/animation_offset_math.py
AnimKey/sliders/tweener.py
AnimKey/sliders/slider_utils.py
AnimKey/core/toolbar.py
```

Tests nuevos:

```text
tests/test_animation_offset_math.py
tests/maya/test_animation_offset_session.py
tests/maya/run_animation_offset_tests.py
```

No refactorizar otros botones o sliders salvo que una prueba demuestre una dependencia directa.

## 32. Compatibilidad publica que se debe conservar

Las siguientes entradas son usadas por toolbar, hotkeys o sliders:

```python
execute(*args, button=None)
is_active()
has_active_offset()
set_button_active(button, is_active)
get_info()
apply_slider_offset_changes(changes)
adjust_keyframes(scan_changed_keys=False)  # wrapper temporal si sigue importado
```

`adjust_keyframes()` puede convertirse en:

```python
def adjust_keyframes(scan_changed_keys=False):
    session = get_active_session()
    if session is None:
        return False
    return session.commit_dirty(reason="legacy_adjust")
```

No debe volver a escanear todas las curvas. El parametro legacy se conserva hasta limpiar todos los call-sites.

## 33. Despliegue a Maya

Primero probar el repositorio de forma explicita. Dentro de Maya:

```python
import os
import sys

repo_root = r"C:\Users\gonza\iCloudDrive\Animacion\personal\ANIMKEY\ANIMKEY"
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
```

Para evitar mezclar modulos antiguos ya importados, la prueba final debe hacerse despues de reiniciar Maya. Recargar solamente `animation_offset.py` no es suficiente cuando cambian `core`, toolbar y sliders.

Una vez validado:

1. Respaldar `C:\Users\gonza\Documents\maya\AnimKey`.
2. Ejecutar el instalador de AnimKey o copiar el paquete completo de forma controlada.
3. No borrar `C:\Users\gonza\Documents\maya\AnimKey_user_data`.
4. Reiniciar Maya.
5. Comprobar `inspect.getfile()`.
6. Comprobar una constante de revision del motor.

Constante propuesta:

```python
ANIMATION_OFFSET_ENGINE_REVISION = 2
```

Validacion:

```python
import inspect
from AnimKey.buttons import animation_offset

print(inspect.getfile(animation_offset))
print(animation_offset.ANIMATION_OFFSET_ENGINE_REVISION)
print(animation_offset.is_active())
```

## 34. Rollback

Antes del despliegue se debe conservar una copia de la instalacion actual. Si aparece una regresion:

1. Cerrar Maya.
2. Restaurar la carpeta completa de AnimKey, no archivos aislados.
3. Mantener `AnimKey_user_data` sin cambios.
4. Reiniciar Maya.
5. Confirmar la revision cargada.

En Git, la nueva implementacion debe quedar en un commit aislado para poder revertirla sin mezclar cambios de otras herramientas.

## 35. Criterios de aceptacion

La implementacion esta terminada solamente cuando cumple todos estos puntos:

- 200 alternancias entre coma y punto no alteran ninguna curva.
- Trasladar durante 10 segundos y soltar no produce movimientos posteriores.
- Rotar durante 10 segundos y soltar no produce rotacion posterior.
- Cruzar 180/-180 no genera un salto de aproximadamente 360 grados.
- Un giro intencional mayor a 360 grados se conserva.
- Un segundo flush sin ediciones no cambia ningun valor.
- Cien ciclos start/stop dejan cero callbacks, timers, jobs y chunks abiertos.
- Navegar sin tracks dirty es O(1).
- Solamente se consultan/aplican los tracks dirty.
- La key driver no recibe doble delta.
- Los cambios de slider de un mismo plug se agrupan.
- Undo revierte un gesto en un paso.
- Redo restaura exactamente la pose.
- BaseAnimation y layers additive/override pasan la matriz.
- Tangentes no cambian involuntariamente.
- Nodos referenciados y nombres duplicados actualizan el objeto correcto.
- No aparecen excepciones en Script Editor durante la matriz completa.
- El archivo cargado por Maya pertenece a la instalacion recien desplegada.
- El repositorio queda limpio despues de tests y artefactos temporales.

Tolerancias numericas propuestas:

```text
Translate/Scale/Custom float: diferencia <= 1e-6
Rotate: diferencia <= 1e-4 grados
Frame matching: diferencia <= 1e-4 frames
```

## 36. Instruccion de continuidad para el modelo 5.5

Al continuar, el siguiente modelo debe:

1. Leer este documento completo.
2. Comprobar nuevamente `git status` y preservar cambios del usuario.
3. Crear primero tests de caracterizacion y una escena minima reproducible.
4. Implementar por fases, ejecutando tests despues de cada una.
5. No editar la copia instalada de Maya hasta que el repositorio pase tests.
6. No declarar resuelto el problema solo porque el modulo compile.
7. Hacer la prueba interactiva final dentro de Maya 2024.
8. Desplegar el paquete completo y verificar la ruta real cargada.
9. Dejar un commit pequeno y explicativo; hacer push despues de validar.

La prioridad no es reducir lineas por si misma. La prioridad es eliminar la realimentacion, hacer determinista el delta y conseguir que el costo dependa de lo que el usuario edito, no del tamano completo de la escena en cada evento.

## 37. Resultado de la implementacion

- Motor dirigido por eventos, sin hilo de polling.
- Rango del Time Slider inclusivo, con seleccion de una o varias keys y subframes.
- Ediciones de multiples keys agrupadas y protegidas contra doble offset.
- Resolucion congelada de BaseAnimation, capas additive y override.
- Conversion de valores compuestos a unidades de curva mediante Maya, incluyendo
  pesos distintos de 1 y rotaciones.
- Callback global de animCurves filtrado por UUID para capturar ediciones directas
  sin escanear curvas ajenas.
- Undo/Redo rebaselina la sesion y elimina trabajo pendiente.
- Tangentes conservadas al aplicar offsets relativos por rangos de indices.
- 32 pruebas automatizadas aprobadas con Maya 2024.
