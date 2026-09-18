"""
================================================================================
AnimKey - Animation Recovery (Anim Crash)

Automatically saves animation checkpoints for recovery in case of crash.
Sophisticated PySide2 window matching AnimKey's design language.
================================================================================
"""

import maya.cmds as cmds
from AnimKey.mods.uiMod import ContextPopupWindow
import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import maya.OpenMayaUI as mui
import json
import os
import time
import hashlib
import threading
from datetime import datetime, timedelta
from collections import defaultdict

from AnimKey.mods.maya_compat import (
    QtCore, QtGui, QtWidgets, wrap_instance as wrapInstance,
)
from AnimKey.mods.storage import atomic_write_json
from AnimKey.core.animation_curve_transfer import paste_curve


QT_NO_BUTTON = getattr(QtCore.Qt, "NoButton", None)
if QT_NO_BUTTON is None:
    QT_NO_BUTTON = QtCore.Qt.MouseButton.NoButton
QT_USER_ROLE = getattr(QtCore.Qt, "UserRole", None)
if QT_USER_ROLE is None:
    QT_USER_ROLE = QtCore.Qt.ItemDataRole.UserRole
QT_SINGLE_SELECTION = getattr(
    QtWidgets.QAbstractItemView, "SingleSelection", None
)
if QT_SINGLE_SELECTION is None:
    QT_SINGLE_SELECTION = (
        QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
    )


# ==============================================================================
# CONFIGURACIÓN
# ==============================================================================

class Config:
    @classmethod
    def get_save_folder(cls):
        from AnimKey.mods import configMod
        user_folder = configMod.get_user_folder_path()
        folder = os.path.join(user_folder, "anim_recovery")
        if not os.path.exists(folder):
            os.makedirs(folder)
        return folder
    
    # A checkpoint of a production shot can contain hundreds of thousands of
    # keys.  Wait for a real pause in the animator's work and keep enough time
    # between full snapshots that recovery never becomes a background loop.
    SAVE_INTERVAL = 120
    IDLE_DELAY = 8.0
    TIMER_INTERVAL_MS = 2000
    MAX_DAYS = 7
    MAX_CHECKPOINTS = 80


_async_checkpoint_capture = None
_async_checkpoint_pending = None
_async_checkpoint_write_in_progress = False


# ==============================================================================
# SISTEMA DE RECOVERY
# ==============================================================================

class RecoverySystem:
    _active = False
    _callbacks = []
    _jobs = []
    _timer = None
    _dirty = False
    _last_change_time = 0.0
    _last_checkpoint_time = 0.0
    _last_checkpoint_path = None
    _change_serial = 0

    @classmethod
    def preferred_enabled(cls):
        try:
            from AnimKey.mods import configMod
            return bool(configMod.get_config().get("crash_recovery_enabled", True))
        except Exception:
            return True

    @classmethod
    def set_preferred_enabled(cls, enabled):
        try:
            from AnimKey.mods import configMod
            configMod.get_config().set("crash_recovery_enabled", bool(enabled))
        except Exception:
            pass

    @classmethod
    def ensure_preferred_state(cls):
        if cls.preferred_enabled():
            cls.start()
        else:
            cls.stop()
        return cls._active

    @classmethod
    def start(cls):
        if cls._active:
            return
        Config.get_save_folder()
        cls._active = True
        invalidate_animated_objects_cache()
        cls._dirty = True
        cls._last_change_time = time.monotonic()
        cls._change_serial += 1
        
        try:
            cb = om2.MSceneMessage.addCallback(
                om2.MSceneMessage.kBeforeOpen, cls._on_before_scene_change
            )
            cls._callbacks.append(cb)
            cb = om2.MSceneMessage.addCallback(
                om2.MSceneMessage.kBeforeNew, cls._on_before_scene_change
            )
            cls._callbacks.append(cb)
            cb = om2.MSceneMessage.addCallback(
                om2.MSceneMessage.kAfterOpen, cls._on_after_scene_change
            )
            cls._callbacks.append(cb)
            cb = om2.MSceneMessage.addCallback(
                om2.MSceneMessage.kAfterNew, cls._on_after_scene_change
            )
            cls._callbacks.append(cb)
            cb = om2.MSceneMessage.addCallback(
                om2.MSceneMessage.kBeforeSave, cls._on_before_save
            )
            cls._callbacks.append(cb)
            cb = om2.MSceneMessage.addCallback(
                om2.MSceneMessage.kAfterSave, cls._on_after_save
            )
            cls._callbacks.append(cb)
            cb = om2.MSceneMessage.addCallback(om2.MSceneMessage.kMayaExiting, cls._on_exit)
            cls._callbacks.append(cb)
        except: pass

        try:
            # This callback only flips a Boolean and invalidates a small name
            # cache.  It does not scan the scene or restart a capture for each
            # key edit, which was the expensive behavior in the newer system.
            cb = oma2.MAnimMessage.addAnimKeyframeEditedCallback(
                cls._on_animation_edited
            )
            cls._callbacks.append(cb)
        except Exception:
            pass

        try:
            cls._jobs.append(cmds.scriptJob(event=['Undo', cls._on_change]))
            cls._jobs.append(cmds.scriptJob(event=['Redo', cls._on_change]))
        except: pass
        
        cls._start_timer()
        # Produce a real first backup without waiting 25 seconds.  This is a
        # single deferred request, not a permanent edit callback.
        try:
            cmds.evalDeferred(cls._tick)
        except Exception:
            pass
        print("AnimKey: Recovery system STARTED")
    
    @classmethod
    def stop(cls):
        if not cls._active:
            cancel_async_checkpoints()
            return
        for cb in cls._callbacks:
            try: om2.MMessage.removeCallback(cb)
            except: pass
        cls._callbacks = []
        for job in cls._jobs:
            try: cmds.scriptJob(kill=job, force=True)
            except: pass
        cls._jobs = []
        if cls._timer:
            try:
                cls._timer.stop()
                cls._timer.deleteLater()
            except Exception:
                pass
            cls._timer = None
        cancel_async_checkpoints()
        cls._active = False
        print("AnimKey: Recovery system STOPPED")
    
    @classmethod
    def is_active(cls):
        return cls._active
    
    @classmethod
    def toggle(cls):
        if cls._active:
            cls.stop()
        else:
            cls.start()
        cls.set_preferred_enabled(cls._active)
        return cls._active
    
    @classmethod
    def _start_timer(cls):
        cls._timer = QtCore.QTimer(QtWidgets.QApplication.instance())
        cls._timer.setSingleShot(True)
        cls._timer.timeout.connect(cls._tick)
        cls._schedule_tick(Config.IDLE_DELAY)

    @classmethod
    def _schedule_tick(cls, delay_seconds=None):
        """Arm one recovery check; remain completely idle otherwise."""
        if not cls._active or cls._timer is None:
            return
        if delay_seconds is None:
            delay_seconds = Config.IDLE_DELAY
        delay_ms = max(1, int(float(delay_seconds) * 1000.0))
        cls._timer.start(delay_ms)

    @classmethod
    def _tick(cls):
        if not cls._active:
            return
        if (
            _async_checkpoint_capture is not None
            or _async_checkpoint_write_in_progress
        ):
            cls._schedule_tick(Config.TIMER_INTERVAL_MS / 1000.0)
            return
        try:
            if cmds.play(query=True, state=True):
                cls._schedule_tick(Config.TIMER_INTERVAL_MS / 1000.0)
                return
        except Exception:
            pass
        try:
            if QtWidgets.QApplication.mouseButtons() != QT_NO_BUTTON:
                cls._schedule_tick(Config.TIMER_INTERVAL_MS / 1000.0)
                return
        except Exception:
            pass
        if not cls._dirty:
            return
        now = time.monotonic()
        idle_remaining = (
            float(Config.IDLE_DELAY) - (now - cls._last_change_time)
        )
        if idle_remaining > 0.0:
            cls._schedule_tick(idle_remaining)
            return
        if cls._last_checkpoint_time:
            interval_remaining = (
                float(Config.SAVE_INTERVAL) -
                (now - cls._last_checkpoint_time)
            )
            if interval_remaining > 0.0:
                cls._schedule_tick(interval_remaining)
                return
        request_checkpoint(auto=True)
    
    @classmethod
    def _on_change(cls, *args):
        cls._dirty = True
        cls._last_change_time = time.monotonic()
        cls._change_serial += 1
        invalidate_animated_objects_cache()
        # A dense slider gesture can emit hundreds of key-edit callbacks in a
        # few milliseconds.  Arm the single-shot timer once; when it fires it
        # uses the latest timestamp and reschedules only the remaining idle
        # delay.  Re-starting QTimer for every key made recovery compete with
        # Tweener even though no checkpoint was being captured yet.
        if cls._timer is None or not cls._timer.isActive():
            cls._schedule_tick(Config.IDLE_DELAY)

    @classmethod
    def _on_animation_edited(cls, *args):
        cls._on_change()

    @classmethod
    def _on_before_scene_change(cls, *args):
        # Never inspect a dependency graph while Maya is replacing it.
        cancel_async_checkpoints(auto_only=True)
        invalidate_animated_objects_cache()
        cls._dirty = False
        if cls._timer:
            cls._timer.stop()

    @classmethod
    def _on_after_scene_change(cls, *args):
        # Recovery commonly starts before the animator opens a shot.  Reset
        # the throttle so every newly opened scene gets its own initial
        # animation checkpoint, even when the file itself is unmodified.
        cancel_async_checkpoints(auto_only=True)
        invalidate_animated_objects_cache()
        cls._last_checkpoint_time = 0.0
        cls._last_checkpoint_path = None
        cls._dirty = True
        cls._last_change_time = time.monotonic()
        cls._change_serial += 1
        cls._schedule_tick(Config.IDLE_DELAY)

    @classmethod
    def _on_before_save(cls, *args):
        # Never compete with Maya's own scene serialization.  A canceled auto
        # capture remains dirty and is retried only after the animator is idle.
        cancel_async_checkpoints(auto_only=True)

    @classmethod
    def _on_after_save(cls, *args):
        # The scene file is safe, but keep an independent animation-only
        # recovery point as animBot does.  It is delayed and chunked, so it
        # never competes with Maya's save operation itself.
        cls._dirty = True
        cls._last_change_time = time.monotonic()
        cls._change_serial += 1
        cls._schedule_tick(Config.IDLE_DELAY)

    @classmethod
    def _checkpoint_completed(cls, filepath, change_serial):
        if not filepath:
            return
        cls._last_checkpoint_time = time.monotonic()
        cls._last_checkpoint_path = filepath
        if int(change_serial) == int(cls._change_serial):
            cls._dirty = False
            if cls._timer:
                cls._timer.stop()
        elif cls._dirty:
            cls._schedule_tick(Config.IDLE_DELAY)
    
    @classmethod
    def _on_exit(cls, *args):
        # Maya is tearing down here.  Avoid DG reads during shutdown.
        cls._dirty = False
        cls.stop()


# ==============================================================================
# UTILIDADES
# ==============================================================================

def get_namespace(obj_name):
    if ':' in obj_name:
        return obj_name.rsplit(':', 1)[0]
    return ""

def get_short_name(obj_name):
    if ':' in obj_name:
        return obj_name.rsplit(':', 1)[1]
    return obj_name

def remap_namespace(obj_name, source_ns, target_ns):
    short = get_short_name(obj_name)
    if target_ns == "(sin namespace)" or target_ns == "":
        return short
    elif target_ns and target_ns != "(mismo)":
        return target_ns + ":" + short
    return obj_name

def get_scene_namespaces():
    namespaces = cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or []
    return sorted([ns for ns in namespaces if ns not in ['UI', 'shared']])

def get_scene_name():
    path = cmds.file(q=True, sn=True)
    if path:
        return os.path.splitext(os.path.basename(path))[0]
    return "untitled"

def get_scene_id():
    path = cmds.file(q=True, sn=True)
    if path:
        path = os.path.normpath(path).lower()
        h = hashlib.md5(path.encode()).hexdigest()[:8]
        name = os.path.splitext(os.path.basename(path))[0]
        return name + "_" + h
    return "untitled_scene"

def get_scene_folder():
    scene_id = get_scene_id()
    folder = os.path.join(Config.get_save_folder(), scene_id)
    if not os.path.exists(folder):
        os.makedirs(folder)
    return folder

def safe_get_ns_names(ns_data):
    if isinstance(ns_data, dict):
        return list(ns_data.keys())
    elif isinstance(ns_data, list):
        return ns_data
    return []

def count_objects_by_namespace(objects):
    ns_counts = defaultdict(int)
    for obj in objects:
        ns = get_namespace(obj) or "(sin namespace)"
        ns_counts[ns] += 1
    return dict(ns_counts)


# ==============================================================================
# FUNCIONES DE ANIMACIÓN
# ==============================================================================

_animated_objects_cache = {"objects": None, "time": 0.0}
ANIMATED_OBJECTS_CACHE_TTL = 10.0  # segundos. Recalcular la lista completa de objetos
                                    # animados es lo más caro de un checkpoint en escenas
                                    # grandes, así que reutilizamos el resultado un rato
                                    # en vez de recorrer todas las curvas en cada save.

def invalidate_animated_objects_cache():
    _animated_objects_cache["objects"] = None
    _animated_objects_cache["time"] = 0.0

def get_animated_objects(force=False):
    now = time.time()
    if (not force and _animated_objects_cache["objects"] is not None and
            (now - _animated_objects_cache["time"]) < ANIMATED_OBJECTS_CACHE_TTL):
        return list(_animated_objects_cache["objects"])

    animated = set()
    curve_types = ['animCurveTL', 'animCurveTA', 'animCurveTU', 'animCurveTT']
    curves = []
    for curve_type in curve_types:
        curves.extend(cmds.ls(type=curve_type) or [])

    # One batched graph query is considerably cheaper than one Maya command
    # per curve (10k+ calls in a production shot).  Keep the old path only as
    # a compatibility fallback for Maya builds that reject list arguments.
    try:
        if curves:
            destinations = cmds.listConnections(
                curves, destination=True, source=False, plugs=True
            ) or []
        else:
            destinations = []
    except Exception:
        destinations = []
        for curve in curves:
            destinations.extend(
                cmds.listConnections(
                    curve, destination=True, source=False, plugs=True
                ) or []
            )
    for destination in destinations:
        obj = destination.split('.', 1)[0]
        if obj:
            animated.add(obj)

    result = list(animated)
    _animated_objects_cache["objects"] = result
    _animated_objects_cache["time"] = now
    return result

def _extract_object_animation(obj):
    if not cmds.objExists(obj):
        return None, 0

    obj_anim = {}
    total_keys = 0
    for curve in (cmds.listConnections(obj, type='animCurve', s=True, d=False) or []):
        for conn in (cmds.listConnections(curve, d=True, s=False, p=True) or []):
            if not conn.startswith(obj + '.'):
                continue
            attr = conn.split('.')[-1]
            try:
                times = cmds.keyframe(curve, q=True, tc=True) or []
                values = cmds.keyframe(curve, q=True, vc=True) or []
                if times:
                    try:
                        in_tangents = cmds.keyTangent(curve, q=True, itt=True) or []
                        out_tangents = cmds.keyTangent(curve, q=True, ott=True) or []
                        in_angles = cmds.keyTangent(curve, q=True, ia=True) or []
                        out_angles = cmds.keyTangent(curve, q=True, oa=True) or []
                    except:
                        in_tangents = []
                        out_tangents = []
                        in_angles = []
                        out_angles = []

                    keys = []
                    for index, (t, v) in enumerate(zip(times, values)):
                        key = {'t': t, 'v': v}
                        if index < len(in_tangents):
                            key['itt'] = in_tangents[index]
                        if index < len(out_tangents):
                            key['ott'] = out_tangents[index]
                        if index < len(in_angles):
                            key['ia'] = in_angles[index]
                        if index < len(out_angles):
                            key['oa'] = out_angles[index]
                        keys.append(key)
                    obj_anim[attr] = keys
                    total_keys += len(keys)
            except:
                pass

    return (obj_anim if obj_anim else None), total_keys

def extract_animation(objects=None):
    if objects is None:
        objects = get_animated_objects()
    if not objects:
        return None
    
    ns_counts = count_objects_by_namespace(objects)
    
    data = {
        'meta': {
            'created': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'scene_path': cmds.file(q=True, sn=True) or "",
            'scene_name': get_scene_name(),
            'objects': 0,
            'keys': 0,
            'namespaces': ns_counts
        },
        'animation': {}
    }
    
    total_keys = 0
    for obj in objects:
        obj_anim, key_count = _extract_object_animation(obj)
        if obj_anim:
            data['animation'][obj] = obj_anim
            total_keys += key_count
    
    data['meta']['objects'] = len(data['animation'])
    data['meta']['keys'] = total_keys
    return data if data['animation'] else None

def save_checkpoint(auto=False, desc=""):
    data = extract_animation()
    if not data:
        return None
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = timestamp + "_" + ("auto" if auto else "manual") + ".json"
    data['meta']['description'] = desc or ("Auto" if auto else "Manual")
    data['meta']['is_auto'] = auto
    
    folder = get_scene_folder()
    filepath = os.path.join(folder, filename)
    
    try:
        atomic_write_json(filepath, data, indent=None, ensure_ascii=False)
        atomic_write_json(
            filepath + ".meta",
            data.get("meta", {}),
            indent=None,
            ensure_ascii=False,
        )
        cleanup_old(folder)
        return filepath
    except Exception as e:
        print(f"AnimKey Recovery Error: {e}")
        return None

def _defer_to_main_thread(func, *args):
    try:
        import maya.utils as maya_utils
        maya_utils.executeDeferred(lambda: func(*args))
    except Exception:
        try:
            QtCore.QTimer.singleShot(0, lambda: func(*args))
        except Exception:
            try:
                func(*args)
            except Exception:
                pass

def _write_checkpoint_payload_async(data, folder, filename, on_done=None):
    def worker():
        filepath = None
        try:
            if not os.path.exists(folder):
                os.makedirs(folder)
            filepath = os.path.join(folder, filename)
            atomic_write_json(
                filepath, data, indent=None, ensure_ascii=False
            )
            atomic_write_json(
                filepath + ".meta",
                data.get("meta", {}),
                indent=None,
                ensure_ascii=False,
            )
            cleanup_old(folder)
        except Exception as e:
            filepath = None
            print(f"AnimKey Recovery Error: {e}")

        if on_done:
            # This completion hook only updates thread-safe Python state.  It
            # is responsible for deferring any Maya/UI work itself.  Keeping
            # the writer state independent from Maya's idle queue prevents a
            # missed executeDeferred from leaving recovery permanently busy.
            try:
                on_done(filepath)
            except Exception as e:
                print(f"AnimKey Recovery completion error: {e}")

    thread = threading.Thread(target=worker)
    thread.daemon = True
    thread.start()

def _async_checkpoint_finished(capture, data, folder, filename, on_done):
    global _async_checkpoint_capture, _async_checkpoint_pending
    global _async_checkpoint_write_in_progress

    if _async_checkpoint_capture is capture:
        _async_checkpoint_capture = None

    def finished(filepath):
        global _async_checkpoint_write_in_progress, _async_checkpoint_pending
        if filepath:
            RecoverySystem._checkpoint_completed(
                filepath, capture.change_serial
            )
        pending_request = _async_checkpoint_pending
        _async_checkpoint_pending = None
        _async_checkpoint_write_in_progress = False
        if not filepath and capture.auto and RecoverySystem._dirty:
            RecoverySystem._schedule_tick(
                Config.TIMER_INTERVAL_MS / 1000.0
            )

        if filepath or on_done or pending_request:
            def notify_and_continue():
                if filepath and RecoverySystem.is_active():
                    update_toolbar_checkpoint_status(filepath)
                if on_done:
                    on_done(filepath)
                if pending_request:
                    request_checkpoint(**pending_request)
            _defer_to_main_thread(notify_and_continue)

    if data:
        _async_checkpoint_write_in_progress = True
        _write_checkpoint_payload_async(
            data, folder, filename, on_done=finished
        )
    else:
        if capture.auto:
            RecoverySystem._last_checkpoint_time = time.monotonic()
            if capture.change_serial == RecoverySystem._change_serial:
                RecoverySystem._dirty = False
        if on_done:
            _defer_to_main_thread(on_done, None)

    if not data:
        pending = _async_checkpoint_pending
        _async_checkpoint_pending = None
        if pending:
            request_checkpoint(**pending)

class _AsyncCheckpointCapture(object):
    """V088's lightweight object-based checkpoint capture.

    Capturing one animated object at a time avoids the newer implementation's
    expensive per-layer/per-curve graph traversal.  The timer yields to Maya
    after a small slice so even large rigs stay interactive.
    """

    AUTO_CHUNK_TIME_BUDGET = 0.003
    MANUAL_CHUNK_TIME_BUDGET = 0.012
    AUTO_TIMER_INTERVAL_MS = 20

    def __init__(self, auto=True, desc="", on_done=None):
        self.auto = auto
        self.desc = desc or ("Auto" if auto else "Manual")
        self.on_done = on_done
        self.change_serial = RecoverySystem._change_serial
        self.objects = get_animated_objects()
        self.index = 0
        self.total_keys = 0
        self.folder = get_scene_folder()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.filename = timestamp + "_" + ("auto" if auto else "manual") + ".json"

        ns_counts = count_objects_by_namespace(self.objects)
        self.data = {
            'meta': {
                'created': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'scene_path': cmds.file(q=True, sn=True) or "",
                'scene_name': get_scene_name(),
                'objects': 0,
                'keys': 0,
                'namespaces': ns_counts,
                'description': self.desc,
                'is_auto': auto,
            },
            'animation': {}
        }

        self.timer = QtCore.QTimer()
        self.timer.setInterval(
            self.AUTO_TIMER_INTERVAL_MS if self.auto else 0
        )
        self.timer.timeout.connect(self._process_chunk)

    def start(self):
        if not self.objects:
            _async_checkpoint_finished(
                self, None, self.folder, self.filename, self.on_done
            )
            return
        self.timer.start()

    def cancel(self):
        try:
            self.timer.stop()
        except Exception:
            pass

    def _finish(self):
        self.timer.stop()
        self.data['meta']['objects'] = len(self.data['animation'])
        self.data['meta']['keys'] = self.total_keys
        data = self.data if self.data['animation'] else None
        _async_checkpoint_finished(
            self, data, self.folder, self.filename, self.on_done
        )

    def _process_chunk(self):
        global _async_checkpoint_capture

        if self.auto:
            # If the shot changed after this snapshot began, discard the stale
            # partial capture.  The service will start one fresh checkpoint
            # only after the animator has been idle for IDLE_DELAY seconds.
            if self.change_serial != RecoverySystem._change_serial:
                self.timer.stop()
                if _async_checkpoint_capture is self:
                    _async_checkpoint_capture = None
                return
            try:
                if cmds.play(query=True, state=True):
                    self.timer.setInterval(100)
                    return
            except Exception:
                pass
            try:
                if QtWidgets.QApplication.mouseButtons() != QT_NO_BUTTON:
                    self.timer.setInterval(100)
                    return
            except Exception:
                pass
            self.timer.setInterval(self.AUTO_TIMER_INTERVAL_MS)

        started = time.perf_counter()
        processed = 0
        time_budget = (
            self.AUTO_CHUNK_TIME_BUDGET
            if self.auto else self.MANUAL_CHUNK_TIME_BUDGET
        )

        while self.index < len(self.objects):
            obj = self.objects[self.index]
            self.index += 1
            obj_anim, key_count = _extract_object_animation(obj)
            if obj_anim:
                self.data['animation'][obj] = obj_anim
                self.total_keys += key_count
            processed += 1

            if (
                processed >= 1
                and (time.perf_counter() - started) >= time_budget
            ):
                return

        self._finish()

def request_checkpoint(auto=True, desc="", on_done=None):
    """
    Queue a checkpoint without blocking Maya for the full extraction/write.
    Maya data is sampled in tiny main-thread chunks; JSON writing and cleanup
    are done on a worker thread.
    """
    global _async_checkpoint_capture, _async_checkpoint_pending
    global _async_checkpoint_write_in_progress

    request = {
        "auto": auto,
        "desc": desc,
        "on_done": on_done,
    }

    if _async_checkpoint_write_in_progress:
        if (
            _async_checkpoint_pending is not None
            and _async_checkpoint_pending.get("on_done") is not None
            and on_done is None
        ):
            return None
        _async_checkpoint_pending = request
        return None

    if _async_checkpoint_capture is not None:
        if not auto and _async_checkpoint_capture.auto:
            _async_checkpoint_capture.cancel()
            _async_checkpoint_capture = None
            _async_checkpoint_pending = None
        else:
            if (
                _async_checkpoint_pending is not None and
                _async_checkpoint_pending.get("on_done") is not None and
                on_done is None
            ):
                return None
            _async_checkpoint_pending = request
            return None

    try:
        _async_checkpoint_capture = _AsyncCheckpointCapture(auto=auto, desc=desc, on_done=on_done)
        _async_checkpoint_capture.start()
    except Exception as e:
        _async_checkpoint_capture = None
        print(f"AnimKey Recovery Error: {e}")
        if on_done:
            _defer_to_main_thread(on_done, None)
    return None

def cancel_async_checkpoints(auto_only=False):
    global _async_checkpoint_capture, _async_checkpoint_pending
    if _async_checkpoint_capture is not None:
        if auto_only and not _async_checkpoint_capture.auto:
            return False
        try:
            _async_checkpoint_capture.cancel()
        except Exception:
            pass
    _async_checkpoint_capture = None
    if not auto_only or (
        _async_checkpoint_pending and _async_checkpoint_pending.get("auto")
    ):
        _async_checkpoint_pending = None
    return True

def load_checkpoint(filepath):
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except:
        return None


def _load_checkpoint_meta(filepath):
    """Read checkpoint metadata without parsing the animation payload."""
    sidecar = filepath + ".meta"
    try:
        with open(sidecar, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # Backwards-compatible fast path for older files without a sidecar.  Meta
    # is the first field written by every AnimCrash format, so raw_decode can
    # stop as soon as that small object is complete.
    decoder = json.JSONDecoder()
    buffer = ""
    try:
        with open(filepath, "r", encoding="utf-8") as stream:
            while len(buffer) < 1024 * 1024:
                chunk = stream.read(16384)
                if not chunk:
                    break
                buffer += chunk
                marker = buffer.find('"meta"')
                if marker < 0:
                    continue
                value_start = buffer.find("{", marker + 6)
                if value_start < 0:
                    continue
                try:
                    meta, _end = decoder.raw_decode(buffer[value_start:])
                except ValueError:
                    continue
                if isinstance(meta, dict):
                    return meta
    except Exception:
        pass
    return {}

def apply_animation(data, namespace_filter="TODOS", target_namespace=None):
    if not data or 'animation' not in data:
        return 0, 0, 0
    
    success, failed, skipped = 0, 0, 0

    layer_metadata = {
        item.get("name"): item
        for item in data.get("meta", {}).get("animation_layers", [])
        if isinstance(item, dict) and item.get("name")
    }

    def ensure_layer(layer_name):
        if not layer_name or layer_name == "BaseAnimation":
            return "BaseAnimation"
        if not cmds.objExists(layer_name):
            metadata = layer_metadata.get(layer_name, {})
            try:
                cmds.animLayer(
                    layer_name,
                    override=bool(metadata.get("override", False)),
                    passthrough=bool(metadata.get("passthrough", True)),
                )
            except Exception:
                return None
        return layer_name

    # Rebuild only the layers used by the requested source rig.  Importing one
    # namespace must not recreate animation layers that belong exclusively to
    # other characters in the checkpoint.
    required_layers = set()
    for source_obj, source_anim in data.get("animation", {}).items():
        source_ns = get_namespace(source_obj) or "(sin namespace)"
        if namespace_filter != "TODOS" and source_ns != namespace_filter:
            continue
        for saved_attr in source_anim.values():
            if not isinstance(saved_attr, dict):
                continue
            for entry in saved_attr.get("curves", []):
                if isinstance(entry, dict):
                    layer_name = entry.get("layer")
                    if layer_name and layer_name != "BaseAnimation":
                        required_layers.add(layer_name)

    pending_parents = list(required_layers)
    while pending_parents:
        layer_name = pending_parents.pop()
        parent_name = layer_metadata.get(layer_name, {}).get("parent")
        if (
            parent_name and parent_name != "BaseAnimation"
            and parent_name not in required_layers
        ):
            required_layers.add(parent_name)
            pending_parents.append(parent_name)

    # Adding a layer to an already-restored direct attribute can otherwise
    # rewire (or replace) the base curve while recovery is still in progress.
    for saved_layer in required_layers:
        ensure_layer(saved_layer)
    for saved_layer in required_layers:
        metadata = layer_metadata.get(saved_layer, {})
        if saved_layer == "BaseAnimation" or not cmds.objExists(saved_layer):
            continue
        parent_layer = metadata.get("parent")
        if parent_layer and cmds.objExists(parent_layer):
            try:
                cmds.animLayer(saved_layer, edit=True, parent=parent_layer)
            except Exception:
                pass
        try:
            cmds.setAttr(saved_layer + ".lock", False)
        except Exception:
            pass
    for source_obj, source_anim in data.get("animation", {}).items():
        source_ns = get_namespace(source_obj) or "(sin namespace)"
        if namespace_filter != "TODOS" and source_ns != namespace_filter:
            continue
        preflight_obj = (
            remap_namespace(source_obj, source_ns, target_namespace)
            if target_namespace and target_namespace != "(mismo)"
            else source_obj
        )
        if not cmds.objExists(preflight_obj):
            continue
        for attr, saved_attr in source_anim.items():
            if not isinstance(saved_attr, dict):
                continue
            plug = preflight_obj + "." + attr
            if not cmds.objExists(plug):
                continue
            for entry in saved_attr.get("curves", []):
                layer_name = entry.get("layer") if isinstance(entry, dict) else None
                if not layer_name or layer_name == "BaseAnimation":
                    continue
                if ensure_layer(layer_name):
                    try:
                        cmds.animLayer(layer_name, edit=True, attribute=plug)
                    except Exception:
                        pass
    
    for obj, obj_anim in data['animation'].items():
        obj_ns = get_namespace(obj) or "(sin namespace)"
        
        if namespace_filter != "TODOS" and obj_ns != namespace_filter:
            skipped += 1
            continue
        
        if target_namespace and target_namespace != "(mismo)":
            target_obj = remap_namespace(obj, obj_ns, target_namespace)
        else:
            target_obj = obj
        
        if not cmds.objExists(target_obj):
            failed += 1
            continue
        
        try:
            recovered_any = False
            for attr, keys in obj_anim.items():
                full_attr = target_obj + "." + attr
                if not cmds.attributeQuery(attr, node=target_obj, exists=True):
                    continue

                curve_entries = (
                    keys.get("curves", []) if isinstance(keys, dict) else []
                )
                if curve_entries:
                    for entry in curve_entries:
                        if not isinstance(entry, dict):
                            continue
                        layer_name = ensure_layer(
                            entry.get("layer") or "BaseAnimation"
                        )
                        curve_data = entry.get("curve")
                        if not layer_name or not isinstance(curve_data, dict):
                            continue
                        pasted, _count = paste_curve(
                            full_attr,
                            curve_data,
                            layer_name=layer_name,
                            clear_existing=True,
                        )
                        recovered_any = recovered_any or bool(pasted)
                    continue

                # Legacy v1 checkpoints stored a single direct curve as a
                # list of {t, v, tangent...} dictionaries.
                if not isinstance(keys, list):
                    continue
                cmds.cutKey(full_attr, clear=True)
                for key in keys:
                    cmds.setKeyframe(full_attr, time=key['t'], value=key['v'])
                    if 'itt' in key:
                        try:
                            cmds.keyTangent(
                                full_attr, time=(key['t'], key['t']),
                                inTangentType=key.get('itt', 'auto'),
                                outTangentType=key.get('ott', 'auto'),
                            )
                        except Exception:
                            pass
                recovered_any = recovered_any or bool(keys)
            if recovered_any:
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    for layer_name, metadata in layer_metadata.items():
        if (
            layer_name == "BaseAnimation"
            or layer_name not in required_layers
            or not cmds.objExists(layer_name)
        ):
            continue
        for attr, value in (
            ("mute", metadata.get("mute")),
            ("solo", metadata.get("solo")),
            ("weight", metadata.get("weight")),
            ("lock", metadata.get("lock")),
        ):
            if value is None:
                continue
            try:
                cmds.setAttr("{}.{}".format(layer_name, attr), value)
            except Exception:
                pass
    
    return success, failed, skipped

def get_checkpoints(folder=None):
    if folder is None:
        folder = get_scene_folder()
    
    if not os.path.exists(folder):
        return []
    
    checkpoints = []
    for filename in os.listdir(folder):
        if not filename.endswith('.json'):
            continue
        filepath = os.path.join(folder, filename)
        try:
            parts = filename.replace('.json', '').split('_')
            dt = datetime.strptime(parts[0] + "_" + parts[1], "%Y%m%d_%H%M%S")
            is_auto = 'auto' in filename
            meta = _load_checkpoint_meta(filepath)
            
            ns_raw = meta.get('namespaces', {})
            ns_names = safe_get_ns_names(ns_raw)
            
            checkpoints.append({
                'path': filepath,
                'datetime': dt,
                'display': dt.strftime("%d %b %Y  -  %H:%M:%S"),
                'date': dt.strftime("%d/%m/%Y"),
                'time': dt.strftime("%H:%M:%S"),
                'is_auto': is_auto,
                'type': "AUTO" if is_auto else "MANUAL",
                'icon': "◉" if is_auto else "◆",
                'description': meta.get('description', ''),
                'objects': meta.get('objects', 0),
                'keys': meta.get('keys', 0),
                'ns_names': ns_names,
                'ns_counts': ns_raw if isinstance(ns_raw, dict) else {},
                'scene_name': meta.get('scene_name', ''),
                'size': os.path.getsize(filepath) / 1024
            })
        except:
            continue
    
    checkpoints.sort(key=lambda x: x['datetime'], reverse=True)
    return checkpoints

def _list_checkpoint_files_light(folder):
    """Lista los checkpoints leyendo SOLO el timestamp del nombre de archivo,
    sin abrir ni parsear el contenido JSON. Mucho más barato que get_checkpoints()
    y es lo único que cleanup_old() necesita para decidir qué borrar."""
    if not os.path.exists(folder):
        return []
    items = []
    for filename in os.listdir(folder):
        if not filename.endswith('.json'):
            continue
        try:
            parts = filename.replace('.json', '').split('_')
            dt = datetime.strptime(parts[0] + "_" + parts[1], "%Y%m%d_%H%M%S")
            items.append((dt, os.path.join(folder, filename)))
        except Exception:
            continue
    items.sort(key=lambda x: x[0], reverse=True)
    return items

def cleanup_old(folder=None):
    if folder is None:
        folder = get_scene_folder()
    items = _list_checkpoint_files_light(folder)
    cutoff = datetime.now() - timedelta(days=Config.MAX_DAYS)

    keep = []
    for dt, path in items:
        if dt < cutoff:
            for candidate in (path, path + ".meta"):
                try: os.remove(candidate)
                except: pass
        else:
            keep.append((dt, path))

    if len(keep) > Config.MAX_CHECKPOINTS:
        for dt, path in keep[Config.MAX_CHECKPOINTS:]:
            for candidate in (path, path + ".meta"):
                try: os.remove(candidate)
                except: pass

def delete_checkpoint(filepath):
    if os.path.exists(filepath):
        os.remove(filepath)
        try:
            os.remove(filepath + ".meta")
        except Exception:
            pass
        return True
    return False


# ==============================================================================
# UI HELPERS
# ==============================================================================

WINDOW_OBJECT = "AnimKey_AnimCrash"
_recovery_window = None

def get_maya_main_window():
    return wrapInstance(int(mui.MQtUtil.mainWindow()), QtWidgets.QWidget)


def update_toolbar_crash_button(is_active):
    """
    Update the toolbar's CRASH button blinking state.
    This imports the toolbar singleton and updates its indicator.
    """
    try:
        from AnimKey.core.toolbar import AnimKeyToolbar
        toolbar = AnimKeyToolbar._instance
        
        if toolbar and hasattr(toolbar, 'update_recovery_indicator'):
            toolbar.update_recovery_indicator(is_active)
    except Exception as e:
        # Silently fail if toolbar is not found
        pass


def update_toolbar_checkpoint_status(filepath):
    """Show that the active recovery service has completed a real write."""
    try:
        from AnimKey.core.toolbar import AnimKeyToolbar
        toolbar = AnimKeyToolbar._instance
        if toolbar and hasattr(toolbar, "update_recovery_checkpoint"):
            toolbar.update_recovery_checkpoint(filepath)
    except Exception:
        pass


class RecoveryWindow(ContextPopupWindow):
    """Animation Recovery Window with modern AnimKey design"""
    
    def __init__(self, anchor_button=None, parent=None):
        super().__init__(anchor_button=anchor_button, parent=parent)
        
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle('Animation Recovery')
        self.setFixedSize(700, 580)
        
        # Frameless window
        
        
        
        self._base_opacity = 1.0
        self._hover_opacity = 1.0
        
        self.checkpoints = []
        self.filtered = []
        self.selected = None
        self.selected_data = None
        self.current_folder = None
        
        self._setup_ui()
        self.position_window()
        self._ensure_visible_on_screen()
        
        # Initial load
        self.current_folder = get_scene_folder()
        QtCore.QTimer.singleShot(100, self._refresh)
        QtCore.QTimer.singleShot(0, self._ensure_visible_on_screen)
        
        self.setWindowOpacity(self._base_opacity)

    def _ensure_visible_on_screen(self):
        """Clamp the recovery window to the visible desktop area."""
        app = QtWidgets.QApplication.instance()
        if app is None:
            return

        pos = self.pos()
        center = QtCore.QPoint(pos.x() + self.width() // 2, pos.y() + self.height() // 2)

        screen = None
        try:
            if hasattr(QtWidgets.QApplication, 'screenAt'):
                screen = QtWidgets.QApplication.screenAt(center)
        except Exception:
            screen = None

        if screen:
            screen_rect = screen.availableGeometry()
        else:
            try:
                desktop = app.desktop()
                screen_rect = desktop.availableGeometry(self)
            except Exception:
                return

        margin = 12
        max_x = screen_rect.right() - self.width() - margin
        max_y = screen_rect.bottom() - self.height() - margin
        min_x = screen_rect.left() + margin
        min_y = screen_rect.top() + margin

        if max_x < min_x:
            x_pos = min_x
        else:
            x_pos = max(min_x, min(pos.x(), max_x))

        if max_y < min_y:
            y_pos = min_y
        else:
            y_pos = max(min_y, min(pos.y(), max_y))

        if x_pos != pos.x() or y_pos != pos.y():
            self.move(x_pos, y_pos)
            self._tail_x = max(20, min(self._tail_x, self.width() - 20))
            self.update()
    
    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, self._tail_height + 1)
        main_layout.setSpacing(0)
        main_layout.setSpacing(0)
        
        # Container
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("mainContainer")
        self.container.setStyleSheet("""
            QFrame#mainContainer {
                background-color: transparent;
                border: none;
            }
        """)
        
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        
        # Header
        header_bar = QtWidgets.QHBoxLayout()
        header_bar.setContentsMargins(10, 10, 10, 10)
        self.title_label = QtWidgets.QLabel("Animation Recovery")
        self.title_label.setStyleSheet("color: #AAA; font-size: 11px; font-weight: 500; border: none;")
        header_bar.addWidget(self.title_label)
        header_bar.addStretch()

        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888;
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #E74C3C;
                color: #FFF;
            }
        """)
        close_btn.clicked.connect(self.close)
        header_bar.addWidget(close_btn)
        container_layout.addLayout(header_bar)
        
        # Header with power toggle
        header = QtWidgets.QWidget()
        header.setStyleSheet("background-color: #404040; border: 1px solid #5A5A5A; border-radius: 6px;")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(14, 8, 14, 8)
        
        self.folder_label = QtWidgets.QLabel(f"📁 {get_scene_name()}")
        self.folder_label.setStyleSheet("color: #a0a7b4; font-size: 11px; font-weight: 500;")
        header_layout.addWidget(self.folder_label)
        
        # Browse folder button
        browse_btn = QtWidgets.QPushButton("📂")
        browse_btn.setFixedSize(26, 26)
        browse_btn.setToolTip("Browse to another recovery folder")
        browse_btn.setCursor(QtCore.Qt.PointingHandCursor)
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #454545;
                color: #A0A7B4;
                border: 1px solid #606060;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #505050; color: #FFF; border-color: #6A6A6A; }
        """)
        browse_btn.clicked.connect(self._browse_folder)
        header_layout.addWidget(browse_btn)
        
        header_layout.addStretch()
        
        # Power toggle
        is_on = RecoverySystem.is_active()
        self.power_btn = QtWidgets.QPushButton("● ON" if is_on else "○ OFF")
        self.power_btn.setFixedSize(70, 28)
        self.power_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._update_power_style(is_on)
        self.power_btn.clicked.connect(self._toggle_power)
        header_layout.addWidget(self.power_btn)
        
        container_layout.addWidget(header)
        
        # Content
        content = QtWidgets.QWidget()
        content.setStyleSheet("background-color: transparent;")
        content_layout = QtWidgets.QHBoxLayout(content)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(12)
        
        # Left panel - Checkpoint list
        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        
        # Filter
        filter_row = QtWidgets.QHBoxLayout()
        filter_label = QtWidgets.QLabel("Filter:")
        filter_label.setStyleSheet("color: #888; font-size: 10px;")
        filter_row.addWidget(filter_label)
        
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItems(["All", "Today", "This Week", "Manual Only", "Auto Only"])
        self.filter_combo.setStyleSheet("""
            QComboBox {
                background-color: #454545;
                color: #F5F5F7;
                border: 1px solid #606060;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 10px;
            }
            QComboBox:hover { border-color: #6A6A6A; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                background-color: #454545;
                color: #FFF;
                border: 1px solid #606060;
                selection-background-color: #3498DB;
            }
        """)
        self.filter_combo.currentTextChanged.connect(self._refresh)
        filter_row.addWidget(self.filter_combo)
        
        filter_row.addStretch()
        
        refresh_btn = QtWidgets.QPushButton("↻")
        refresh_btn.setFixedSize(24, 24)
        refresh_btn.setCursor(QtCore.Qt.PointingHandCursor)
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #454545;
                color: #A0A7B4;
                border: 1px solid #606060;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #505050; color: #FFF; border-color: #6A6A6A; }
        """)
        refresh_btn.clicked.connect(self._refresh)
        filter_row.addWidget(refresh_btn)
        
        left_layout.addLayout(filter_row)
        
        # Checkpoint list
        self.checkpoint_list = QtWidgets.QListWidget()
        self.checkpoint_list.setStyleSheet("""
            QListWidget {
                background-color: #404040;
                color: #CCC;
                border: 1px solid #565656;
                border-radius: 6px;
                padding: 4px;
                font-size: 10px;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 4px;
                margin: 2px 0px;
            }
            QListWidget::item:selected {
                background-color: #3498DB;
                color: #FFF;
            }
            QListWidget::item:hover:!selected {
                background-color: #4A4A4A;
            }
        """)
        self.checkpoint_list.itemClicked.connect(self._on_select)
        self.checkpoint_list.itemDoubleClicked.connect(self._on_recover)
        left_layout.addWidget(self.checkpoint_list)
        
        # Count label
        self.count_label = QtWidgets.QLabel("0 checkpoints")
        self.count_label.setStyleSheet("color: #666; font-size: 9px;")
        left_layout.addWidget(self.count_label)
        
        content_layout.addWidget(left_panel, stretch=1)
        
        # Right panel - Details
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        
        # Info section
        info_frame = QtWidgets.QFrame()
        info_frame.setStyleSheet("""
            QFrame {
                background-color: #404040;
                border: 1px solid #565656;
                border-radius: 6px;
            }
        """)
        info_layout = QtWidgets.QVBoxLayout(info_frame)
        info_layout.setContentsMargins(12, 10, 12, 10)
        info_layout.setSpacing(6)
        
        info_title = QtWidgets.QLabel("⚡ Checkpoint Info")
        info_title.setStyleSheet("color: #f5f5f7; font-size: 11px; font-weight: 700;")
        info_layout.addWidget(info_title)
        
        self.info_labels = {}
        for key in ['Date', 'Time', 'Type', 'Objects', 'Keyframes']:
            row = QtWidgets.QHBoxLayout()
            label = QtWidgets.QLabel(f"{key}:")
            label.setStyleSheet("color: #888; font-size: 10px;")
            label.setFixedWidth(70)
            row.addWidget(label)
            
            value = QtWidgets.QLabel("--")
            value.setStyleSheet("color: #CCC; font-size: 10px;")
            row.addWidget(value)
            row.addStretch()
            
            info_layout.addLayout(row)
            self.info_labels[key] = value
        
        right_layout.addWidget(info_frame)
        
        # Namespace section
        ns_frame = QtWidgets.QFrame()
        ns_frame.setStyleSheet("""
            QFrame {
                background-color: #404040;
                border: 1px solid #565656;
                border-radius: 6px;
            }
        """)
        ns_layout = QtWidgets.QVBoxLayout(ns_frame)
        ns_layout.setContentsMargins(12, 10, 12, 10)
        ns_layout.setSpacing(6)
        
        ns_title = QtWidgets.QLabel("🎭 Source Rig / Namespace")
        ns_title.setStyleSheet("color: #f5f5f7; font-size: 11px; font-weight: 700;")
        ns_layout.addWidget(ns_title)
        
        self.ns_list = QtWidgets.QListWidget()
        self.ns_list.setMaximumHeight(80)
        self.ns_list.setSelectionMode(
            QT_SINGLE_SELECTION
        )
        self.ns_list.setStyleSheet("""
            QListWidget {
                background-color: #3A3A3A;
                color: #CCC;
                border: 1px solid #565656;
                border-radius: 4px;
                font-size: 10px;
            }
            QListWidget::item { padding: 4px; }
            QListWidget::item:selected { background-color: #3498DB; color: #FFF; }
        """)
        self.ns_list.currentItemChanged.connect(
            self._on_namespace_selection_changed
        )
        ns_layout.addWidget(self.ns_list)
        
        # Source/Target remap
        remap_row = QtWidgets.QHBoxLayout()
        
        remap_label = QtWidgets.QLabel("Remap to:")
        remap_label.setStyleSheet("color: #888; font-size: 10px;")
        remap_row.addWidget(remap_label)
        
        self.target_combo = QtWidgets.QComboBox()
        self.target_combo.addItem("(same)")
        for ns in get_scene_namespaces():
            self.target_combo.addItem(ns)
        self.target_combo.addItem("(no namespace)")
        self.target_combo.setEnabled(False)
        self.target_combo.setStyleSheet("""
            QComboBox {
                background-color: #454545;
                color: #F5F5F7;
                border: 1px solid #606060;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 10px;
            }
        """)
        remap_row.addWidget(self.target_combo)
        
        ns_layout.addLayout(remap_row)
        right_layout.addWidget(ns_frame)
        
        right_layout.addStretch()
        
        content_layout.addWidget(right_panel, stretch=1)
        container_layout.addWidget(content)
        
        # Bottom buttons
        button_bar = QtWidgets.QWidget()
        button_bar.setStyleSheet("background-color: transparent; border: none;")
        button_layout = QtWidgets.QHBoxLayout(button_bar)
        button_layout.setContentsMargins(14, 10, 14, 12)
        button_layout.setSpacing(8)
        
        # Save button
        save_btn = QtWidgets.QPushButton("💾 Save Now")
        save_btn.setFixedHeight(32)
        save_btn.setCursor(QtCore.Qt.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #454545;
                color: #FFF;
                border: 1px solid #606060;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 500;
                padding: 0 16px;
            }
            QPushButton:hover { background-color: #3498DB; border-color: #3498DB; }
        """)
        save_btn.clicked.connect(self._on_save)
        button_layout.addWidget(save_btn)
        
        button_layout.addStretch()
        
        # Recover button
        recover_btn = QtWidgets.QPushButton("⚡ Recover")
        recover_btn.setFixedHeight(32)
        recover_btn.setCursor(QtCore.Qt.PointingHandCursor)
        recover_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498DB;
                color: #FFF;
                border: 1px solid #3498DB;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                padding: 0 20px;
            }
            QPushButton:hover { background-color: #4AA3DF; border-color: #4AA3DF; }
        """)
        recover_btn.clicked.connect(self._on_recover)
        button_layout.addWidget(recover_btn)
        
        # Delete button
        delete_btn = QtWidgets.QPushButton("🗑")
        delete_btn.setFixedSize(32, 32)
        delete_btn.setCursor(QtCore.Qt.PointingHandCursor)
        delete_btn.setStyleSheet("""
            QPushButton {
                background-color: #bf616a;
                color: #FFF;
                border: none;
                border-radius: 6px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #cf717a; }
        """)
        delete_btn.clicked.connect(self._on_delete)
        button_layout.addWidget(delete_btn)
        
        container_layout.addWidget(button_bar)
        main_layout.addWidget(self.container)
    
    def _update_power_style(self, is_on):
        if is_on:
            self.power_btn.setText("● ON")
            self.power_btn.setStyleSheet("""
                QPushButton {
                    background-color: #3E5A3E;
                    color: #a3be8c;
                    border: 1px solid #a3be8c;
                    border-radius: 4px;
                    font-size: 10px;
                    font-weight: 600;
                }
                QPushButton:hover { background-color: #4A684A; }
            """)
        else:
            self.power_btn.setText("○ OFF")
            self.power_btn.setStyleSheet("""
                QPushButton {
                    background-color: #5A3E3E;
                    color: #bf616a;
                    border: 1px solid #bf616a;
                    border-radius: 4px;
                    font-size: 10px;
                    font-weight: 600;
                }
                QPushButton:hover { background-color: #684A4A; }
            """)
    
    def _toggle_power(self):
        is_on = RecoverySystem.toggle()
        self._update_power_style(is_on)
        
        # Update the toolbar's CRASH button blinking
        update_toolbar_crash_button(is_on)
        
        cmds.inViewMessage(
            amg=f"<span style='color:{'#a3be8c' if is_on else '#bf616a'}'>Recovery {'STARTED' if is_on else 'STOPPED'}</span>",
            pos='topCenter',
            fade=True,
            fadeStayTime=1500
        )
    
    def _refresh(self, *args):
        self.checkpoints = get_checkpoints(self.current_folder)
        
        # Apply filter
        filter_text = self.filter_combo.currentText()
        now = datetime.now()
        
        if filter_text == "Today":
            self.filtered = [c for c in self.checkpoints if c['datetime'].date() == now.date()]
        elif filter_text == "This Week":
            self.filtered = [c for c in self.checkpoints if c['datetime'] > now - timedelta(days=7)]
        elif filter_text == "Manual Only":
            self.filtered = [c for c in self.checkpoints if not c['is_auto']]
        elif filter_text == "Auto Only":
            self.filtered = [c for c in self.checkpoints if c['is_auto']]
        else:
            self.filtered = self.checkpoints
        
        # Update list
        self.checkpoint_list.clear()
        for cp in self.filtered:
            ns_str = ", ".join(cp.get('ns_names', [])[:2]) or "-"
            if len(cp.get('ns_names', [])) > 2:
                ns_str += "..."
            
            text = f"{cp['icon']}  {cp['display']}  │  {cp['objects']} objs  │  {ns_str}"
            self.checkpoint_list.addItem(text)
        
        self.count_label.setText(f"{len(self.filtered)} checkpoints")
    
    def _on_select(self, item):
        idx = self.checkpoint_list.row(item)
        if idx >= 0 and idx < len(self.filtered):
            self.selected = self.filtered[idx]
            # Keep selection instant even for very large layered scenes.  The
            # full payload is loaded only when Recover is actually requested.
            self.selected_data = None
            self._update_details()
    
    def _update_details(self):
        if not self.selected:
            return
        
        cp = self.selected
        self.info_labels['Date'].setText(cp['date'])
        self.info_labels['Time'].setText(cp['time'])
        self.info_labels['Type'].setText(cp['type'])
        self.info_labels['Objects'].setText(str(cp['objects']))
        self.info_labels['Keyframes'].setText(str(cp['keys']))
        
        # Update namespace list
        self.ns_list.clear()
        ns_counts = cp.get("ns_counts", {})
        all_item = QtWidgets.QListWidgetItem(
            "TODOS / All rigs  ({} objs)".format(cp.get("objects", 0))
        )
        all_item.setData(QT_USER_ROLE, "TODOS")
        self.ns_list.addItem(all_item)
        for ns, count in sorted(ns_counts.items()):
            item = QtWidgets.QListWidgetItem(f"{ns}  ({count} objs)")
            item.setData(QT_USER_ROLE, ns)
            self.ns_list.addItem(item)
        self.ns_list.setCurrentRow(0)

    def _selected_source_namespace(self):
        item = self.ns_list.currentItem()
        if item is None:
            return "TODOS"
        namespace = item.data(QT_USER_ROLE)
        return namespace or "TODOS"

    def _on_namespace_selection_changed(self, *args):
        # Remapping several source rigs into one namespace would overwrite
        # them on top of each other, so target remapping is available only
        # when one concrete source rig is selected.
        source_namespace = self._selected_source_namespace()
        can_remap = source_namespace != "TODOS"
        if not can_remap:
            self.target_combo.setCurrentIndex(0)
        self.target_combo.setEnabled(can_remap)
    
    def _on_recover(self, *args):
        if not self.selected:
            cmds.warning("AnimKey: Please select a checkpoint first.")
            return
        if not self.selected_data:
            self.selected_data = load_checkpoint(self.selected['path'])
        if not self.selected_data:
            cmds.warning("AnimKey: Could not read the selected checkpoint.")
            return
        
        target_label = self.target_combo.currentText()
        source_ns = self._selected_source_namespace()
        target_ns = None if target_label == "(same)" else target_label
        if target_ns == "(no namespace)":
            target_ns = "(sin namespace)"
        
        cmds.undoInfo(openChunk=True)
        try:
            success, failed, skipped = apply_animation(
                self.selected_data,
                namespace_filter=source_ns,
                target_namespace=target_ns
            )
            
            cmds.inViewMessage(
                amg=(
                    "<span style='color:#a3be8c'>Recovered {} objects"
                    " from {}</span>"
                ).format(success, source_ns),
                pos='topCenter',
                fade=True,
                fadeStayTime=2000
            )
        finally:
            cmds.undoInfo(closeChunk=True)
    
    def _on_save(self):
        request_checkpoint(auto=False, desc="Manual save", on_done=self._on_manual_save_done)

    def _on_manual_save_done(self, filepath):
        if filepath:
            self._refresh()
            cmds.inViewMessage(
                amg="<span style='color:#a3be8c'>Checkpoint Saved</span>",
                pos='topCenter',
                fade=True,
                fadeStayTime=1500
            )
        else:
            cmds.warning("AnimKey: No animation to save.")
    
    def _on_delete(self):
        if not self.selected:
            cmds.warning("AnimKey: Please select a checkpoint first.")
            return
        
        result = cmds.confirmDialog(
            title="Delete Checkpoint",
            message="Are you sure you want to delete this checkpoint?",
            button=["Delete", "Cancel"],
            defaultButton="Cancel"
        )
        
        if result == "Delete":
            delete_checkpoint(self.selected['path'])
            self.selected = None
            self.selected_data = None
            self._refresh()
    
    def _browse_folder(self):
        """Browse to a different recovery folder"""
        # Start from the anim_recovery root folder
        start_folder = Config.get_save_folder()
        
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Recovery Folder",
            start_folder,
            QtWidgets.QFileDialog.ShowDirsOnly
        )
        
        if folder:
            self.current_folder = folder
            # Update label to show folder name
            folder_name = os.path.basename(folder) or folder
            self.folder_label.setText(f"📁 {folder_name}")
            self._refresh()
            
            # Show message
            cmds.inViewMessage(
                amg=f"<span style='color:#88c0d0'>Loaded: {folder_name}</span>",
                pos='topCenter',
                fade=True,
                fadeStayTime=1500
            )
    
    def enterEvent(self, e):
        self._animate(self._hover_opacity)
        
    def leaveEvent(self, e):
        self._animate(self._base_opacity)
        
    def _animate(self, val):
        anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(150)
        anim.setEndValue(val)
        anim.start(QtCore.QPropertyAnimation.DeleteWhenStopped)
        self._anim = anim
    
    def closeEvent(self, event):
        global _recovery_window
        _recovery_window = None
        super(RecoveryWindow, self).closeEvent(event)


# ==============================================================================
# PUBLIC API
# ==============================================================================

def show(anchor_button=None):
    """Open the Recovery window"""
    global _recovery_window
    from AnimKey.mods import uiMod
    uiMod.close_animkey_tool_windows(except_widget=_recovery_window)
    
    if _recovery_window is not None:
        existing = uiMod.show_existing_animkey_tool_window(_recovery_window, anchor_button)
        if existing is not None:
            _recovery_window = existing
            return _recovery_window
        _recovery_window = None
    
    if cmds.window(WINDOW_OBJECT, exists=True):
        cmds.deleteUI(WINDOW_OBJECT)
    
    _recovery_window = RecoveryWindow(anchor_button=anchor_button, parent=get_maya_main_window())
    _recovery_window.show()
    _recovery_window._ensure_visible_on_screen()
    _recovery_window.raise_()
    return _recovery_window


def execute(*args, button=None):
    """Main entry point for the button"""
    from AnimKey.core.executionGuard import require_animkey_context
    if not require_animkey_context("AnimKey.buttons.animCrash.execute"):
        return None
    return show(anchor_button=button)


def get_info():
    """Return button information for the toolbar"""
    return {
        "name": "Anim Crash",
        "tooltip": "Animation Recovery - Save and restore animation checkpoints",
        "icon": "anim_crash.svg",
        "shortcut": None,
    }
