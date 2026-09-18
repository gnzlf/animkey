"""Deferred cache runtime for AnimKey motion trails.

The runtime follows Animo Tracify's architecture: Maya scene evaluation happens
outside Viewport 2.0 draw callbacks, only the visible window is built eagerly,
and missing or dirty samples are filled in small deferred batches.
"""

import json
import math
import sys

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds
import maya.utils as maya_utils


DEFAULT_VISIBLE_RANGE = 18
# A cache write serializes JSON and invalidates the Viewport 2.0 draw cache.
# Larger deferred batches keep that work bounded without flooding Maya with a
# write/refresh pair for every handful of samples.
FILL_BATCH_SIZE = 16
POSITION_TOLERANCE = 0.0001
CACHE_SCHEMA_VERSION = 2

_trackers = {}
_cache = {}
_camera_transforms = {}
_jobs = []
_attr_jobs = []
_fill_pending = {}
_fill_scheduled = False
_check_job = None
_camera_dirty = set()
_camera_check_job = None
_source_dirty = set()
_source_dirty_scheduled = False
_generation = 0
_scene_callbacks = []
_scene_save_in_progress = False
_scene_save_resume_scheduled = False


def _canonical_node(node):
    if not node:
        return None
    try:
        matches = cmds.ls(node, long=True) or []
        return matches[0] if matches else node
    except Exception:
        return node


def _frame_key(frame):
    value = float(frame)
    rounded = round(value)
    if abs(value - rounded) < 0.000001:
        return str(int(rounded))
    return ("{:.6f}".format(value)).rstrip("0").rstrip(".")


def _frame_from_key(value):
    try:
        return float(value)
    except Exception:
        return 0.0


def _current_frame():
    try:
        return float(oma.MAnimControl.currentTime().value)
    except Exception:
        return float(cmds.currentTime(query=True))


def _defer_low(callback):
    """Run cache work after the active animation edit has finished."""
    try:
        cmds.evalDeferred(callback, lowestPriority=True)
        return True
    except TypeError:
        try:
            cmds.evalDeferred(callback)
            return True
        except Exception:
            pass
    except Exception:
        pass
    try:
        maya_utils.executeDeferred(callback)
        return True
    except Exception:
        return False


def _active_camera_transform():
    camera = None
    try:
        panel = cmds.getPanel(withFocus=True)
        if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
            camera = cmds.modelPanel(panel, query=True, camera=True)
    except Exception:
        camera = None

    if not camera:
        try:
            for panel in cmds.getPanel(type="modelPanel") or []:
                candidate = cmds.modelPanel(panel, query=True, camera=True)
                if candidate:
                    camera = candidate
                    break
        except Exception:
            camera = None

    if not camera:
        return None
    try:
        if cmds.nodeType(camera) == "camera":
            parents = cmds.listRelatives(camera, parent=True, fullPath=True) or []
            return parents[0] if parents else None
    except Exception:
        pass
    return _canonical_node(camera)


def _world_pivot_at(source, frame=None):
    """Return the animated rotate-pivot position using Animo's evaluation path."""
    try:
        kwargs = {} if frame is None else {"time": float(frame)}
        translate = cmds.getAttr(source + ".translate", **kwargs)[0]
        pivot_translate = cmds.getAttr(source + ".rotatePivotTranslate", **kwargs)[0]
        pivot = cmds.getAttr(source + ".rotatePivot", **kwargs)[0]
        parent_matrix = cmds.getAttr(source + ".parentMatrix[0]", **kwargs)
        local_point = om.MPoint(
            translate[0] + pivot_translate[0] + pivot[0],
            translate[1] + pivot_translate[1] + pivot[1],
            translate[2] + pivot_translate[2] + pivot[2],
        )
        point = local_point * om.MMatrix(parent_matrix)
        return [float(point.x), float(point.y), float(point.z)]
    except Exception:
        pass

    try:
        kwargs = {} if frame is None else {"time": float(frame)}
        matrix = cmds.getAttr(source + ".worldMatrix[0]", **kwargs)
        return [float(matrix[12]), float(matrix[13]), float(matrix[14])]
    except Exception:
        return None


def _camera_matrix_at(camera, frame=None):
    if not camera:
        return None
    try:
        kwargs = {} if frame is None else {"time": float(frame)}
        return cmds.getAttr(camera + ".worldMatrix[0]", **kwargs)
    except Exception:
        return None


def _positions_at(source, camera, frame=None):
    world_point = _world_pivot_at(source, frame)
    if world_point is None:
        return None, None

    camera_point = None
    camera_matrix = _camera_matrix_at(camera, frame)
    if camera_matrix is not None:
        try:
            point = om.MPoint(*world_point) * om.MMatrix(camera_matrix).inverse()
            camera_point = [float(point.x), float(point.y), float(point.z)]
        except Exception:
            camera_point = None
    return world_point, camera_point


def _keyframes(source, start, end):
    frames = set()
    selection = om.MSelectionList()
    try:
        selection.add(source)
        try:
            node = selection.getDagPath(0).node()
        except Exception:
            node = selection.getDependNode(0)
        iterator = om.MItDependencyGraph(
            node,
            om.MFn.kAnimCurve,
            om.MItDependencyGraph.kUpstream,
            om.MItDependencyGraph.kDepthFirst,
            om.MItDependencyGraph.kNodeLevel,
        )
        while not iterator.isDone():
            curve = oma.MFnAnimCurve(iterator.currentNode())
            for index in range(curve.numKeys):
                frame = float(curve.input(index).value)
                if float(start) <= frame <= float(end):
                    frames.add(frame)
            iterator.next()
    except Exception:
        pass

    if not frames:
        try:
            raw = cmds.keyframe(
                source, query=True, time=(float(start), float(end)), timeChange=True
            ) or []
            frames.update(float(frame) for frame in raw)
        except Exception:
            pass
    return sorted(frames)


def _sample_frames(start, end, increment=1, density=1):
    start = float(start)
    end = float(end)
    if end < start:
        start, end = end, start
    step = max(0.001, float(max(1, int(increment))) / float(max(1, int(density))))
    count = int(math.floor(((end - start) / step) + 0.000001))
    frames = [start + (index * step) for index in range(count + 1)]
    if not frames or abs(frames[-1] - end) > 0.000001:
        frames.append(end)
    return [round(frame, 6) for frame in frames]


def _visible_bounds(tracker, current_frame, bootstrap=False):
    start = float(tracker["start"])
    end = float(tracker["end"])
    display_range = int(tracker.get("display_range", DEFAULT_VISIBLE_RANGE))
    if display_range <= 0 and not bootstrap:
        return start, end
    radius = display_range if display_range > 0 else DEFAULT_VISIBLE_RANGE
    return max(start, current_frame - radius), min(end, current_frame + radius)


def _frames_in_bounds(tracker, start, end, include_keys=True):
    frames = [
        frame
        for frame in _sample_frames(
            tracker["start"],
            tracker["end"],
            tracker.get("increment", 1),
            tracker.get("density", 1),
        )
        if float(start) - 0.000001 <= frame <= float(end) + 0.000001
    ]
    if include_keys:
        frames.extend(
            frame
            for frame in tracker.get("keyframes", [])
            if float(start) - 0.000001 <= frame <= float(end) + 0.000001
        )
    return sorted(set(round(float(frame), 6) for frame in frames))


def _build_fill_order(frames, current_frame):
    """Prioritize current, then three past and two future samples per round."""
    ordered = sorted(set(float(frame) for frame in frames))
    if not ordered:
        return []
    current_frame = float(current_frame)
    current = min(ordered, key=lambda frame: abs(frame - current_frame))
    left = [frame for frame in ordered if frame < current]
    right = [frame for frame in ordered if frame > current]
    left.reverse()
    result = [current]
    left_index = 0
    right_index = 0
    while left_index < len(left) or right_index < len(right):
        result.extend(left[left_index:left_index + 3])
        left_index += 3
        result.extend(right[right_index:right_index + 2])
        right_index += 2
    return result


def _empty_cache(tracker, camera):
    return {
        "schemaVersion": CACHE_SCHEMA_VERSION,
        "positions": {},
        "cameraPositions": {},
        "frames": [],
        "keyframes": list(tracker.get("keyframes", [])),
        "start": float(tracker["start"]),
        "end": float(tracker["end"]),
        "cameraSpace": bool(camera),
        "cameraTransform": camera,
    }


def _clear_parsed_draw_cache(shape=None):
    for module_name, module in list(sys.modules.items()):
        if not module_name.endswith("animKeyTrailPlugin"):
            continue
        store = getattr(module, "_PARSED_CACHE_STORE", None)
        if not isinstance(store, dict):
            continue
        if shape is None:
            store.clear()
        else:
            store.pop(shape.split("|")[-1], None)


def _sync_cache(shape):
    # cacheData is serialized with the scene.  A queued trail update must not
    # write to it while Maya owns the file-save transaction.
    if _scene_save_in_progress:
        return
    cache = _cache.get(shape)
    if cache is None or not cmds.objExists(shape):
        return
    cache["frames"] = sorted(
        _frame_from_key(key) for key in cache.get("positions", {}).keys()
    )
    undo_state = None
    try:
        undo_state = bool(cmds.undoInfo(query=True, state=True))
        cmds.undoInfo(stateWithoutFlush=False)
    except Exception:
        undo_state = None
    try:
        payload = json.dumps(cache, separators=(",", ":"), sort_keys=False)
        cmds.setAttr(shape + ".cacheData", payload, type="string")
        _clear_parsed_draw_cache(shape)
        try:
            cmds.dgdirty(shape)
        except Exception:
            pass
    finally:
        if undo_state is not None:
            try:
                cmds.undoInfo(stateWithoutFlush=undo_state)
            except Exception:
                pass


def _sample_into_cache(shape, frames):
    tracker = _trackers.get(shape)
    cache = _cache.get(shape)
    if not tracker or cache is None:
        return False
    source = tracker["source"]
    camera = _camera_transforms.get(shape)
    positions = cache.setdefault("positions", {})
    camera_positions = cache.setdefault("cameraPositions", {})
    changed = False
    for frame in frames:
        world_point, camera_point = _positions_at(source, camera, frame)
        key = _frame_key(frame)
        if world_point is not None:
            positions[key] = world_point
            changed = True
        if camera_point is not None:
            camera_positions[key] = camera_point
        elif key in camera_positions:
            camera_positions.pop(key, None)
    return changed


def _build_visible_cache(shape):
    if _scene_save_in_progress:
        return
    tracker = _trackers.get(shape)
    if not tracker or not cmds.objExists(tracker["source"]):
        return
    tracker["keyframes"] = _keyframes(
        tracker["source"], tracker["start"], tracker["end"]
    )
    camera = _camera_transforms.get(shape)
    _cache[shape] = _empty_cache(tracker, camera)
    current = _current_frame()
    window_start, window_end = _visible_bounds(tracker, current, bootstrap=True)
    frames = _frames_in_bounds(tracker, window_start, window_end)
    _sample_into_cache(shape, frames)
    _sync_cache(shape)

    if int(tracker.get("display_range", DEFAULT_VISIBLE_RANGE)) <= 0:
        all_frames = _frames_in_bounds(tracker, tracker["start"], tracker["end"])
        missing = [frame for frame in all_frames if _frame_key(frame) not in _cache[shape]["positions"]]
        _queue_fill(shape, _build_fill_order(missing, current), force=False)


def _queue_fill(shape, frames, force=False):
    global _fill_pending
    if not frames or shape not in _trackers:
        return
    pending = _fill_pending.setdefault(shape, {"frames": [], "force": False})
    existing = set(pending["frames"])
    pending["frames"].extend(frame for frame in frames if frame not in existing)
    pending["force"] = bool(pending["force"] or force)
    _schedule_fill()


def _schedule_fill():
    global _fill_scheduled
    if _fill_scheduled or not _fill_pending:
        return
    _fill_scheduled = True
    if not _defer_low(_process_fill):
        _fill_scheduled = False


def _process_fill():
    global _fill_scheduled
    _fill_scheduled = False
    if _scene_save_in_progress:
        return
    if not _fill_pending:
        return
    changed = False
    for shape in list(_fill_pending):
        pending = _fill_pending.get(shape)
        tracker = _trackers.get(shape)
        if not pending or not tracker or not cmds.objExists(shape) or not cmds.objExists(tracker["source"]):
            _fill_pending.pop(shape, None)
            continue
        frames = pending["frames"]
        batch = frames[:FILL_BATCH_SIZE]
        del frames[:FILL_BATCH_SIZE]
        if not frames:
            _fill_pending.pop(shape, None)
        if _sample_into_cache(shape, batch):
            _sync_cache(shape)
            changed = True
    if changed:
        try:
            cmds.refresh(currentView=True)
        except Exception:
            pass
    if _fill_pending:
        _schedule_fill()


def _clear_fill_queue():
    global _fill_pending, _fill_scheduled, _source_dirty, _source_dirty_scheduled
    _fill_pending = {}
    _fill_scheduled = False
    _source_dirty = set()
    _source_dirty_scheduled = False


def _window_frames(shape, bootstrap=False):
    tracker = _trackers.get(shape)
    if not tracker:
        return []
    current = _current_frame()
    start, end = _visible_bounds(tracker, current, bootstrap=bootstrap)
    return _frames_in_bounds(tracker, start, end)


def _shape_for_identifier(identifier):
    if identifier in _trackers:
        return identifier
    for shape, tracker in _trackers.items():
        if identifier in (tracker.get("container"), tracker.get("source")):
            return shape
    return None


def mark_dirty(identifier, dirty_range=None, refresh_keys=True):
    if _scene_save_in_progress:
        return
    shape = _shape_for_identifier(identifier)
    if not shape:
        return
    tracker = _trackers.get(shape)
    cache = _cache.get(shape)
    if not tracker or cache is None:
        return
    if refresh_keys:
        tracker["keyframes"] = _keyframes(
            tracker["source"], tracker["start"], tracker["end"]
        )
        cache["keyframes"] = list(tracker["keyframes"])

    if dirty_range and len(dirty_range) >= 2:
        start, end = sorted((float(dirty_range[0]), float(dirty_range[1])))
        cached_frames = [
            frame for frame in cache.get("frames", [])
            if start - 0.000001 <= float(frame) <= end + 0.000001
        ]
        dirty_frames = _frames_in_bounds(tracker, start, end)
        frames = sorted(set(cached_frames + dirty_frames))
    else:
        frames = _window_frames(shape)
    _queue_fill(shape, _build_fill_order(frames, _current_frame()), force=True)
    # Persist key-marker changes once. Position samples are persisted by the
    # deferred fill batch, preventing an expensive JSON write per DG event.
    if refresh_keys:
        _sync_cache(shape)


def _on_source_changed(shape):
    if shape not in _trackers:
        return
    _queue_source_dirty(shape)


def _queue_source_dirty(shape):
    """Collapse repeated source DG changes into one post-edit cache check."""
    global _source_dirty_scheduled
    if shape not in _trackers:
        return
    _source_dirty.add(shape)
    if _source_dirty_scheduled:
        return
    _source_dirty_scheduled = True
    if not _defer_low(_flush_source_dirty):
        _source_dirty_scheduled = False


def _flush_source_dirty():
    global _source_dirty, _source_dirty_scheduled
    dirty_shapes = set(_source_dirty)
    _source_dirty = set()
    _source_dirty_scheduled = False
    current_frame = _current_frame()
    for shape in dirty_shapes:
        tracker = _trackers.get(shape)
        cache = _cache.get(shape)
        if (not tracker or cache is None or not cmds.objExists(shape)
                or not cmds.objExists(tracker["source"])):
            continue
        current_world, _camera = _positions_at(
            tracker["source"], _camera_transforms.get(shape), None
        )
        cached = cache.get("positions", {}).get(_frame_key(current_frame))
        if _position_changed(current_world, cached):
            # World-matrix edits are common while manipulating controls. Only
            # the current sample is stale; curve-edit callbacks handle the
            # wider neighbouring key interval after the operation completes.
            mark_dirty(
                shape,
                dirty_range=(current_frame, current_frame),
                refresh_keys=False,
            )


def _on_time_changed():
    if _scene_save_in_progress:
        return
    current = _current_frame()
    for shape, tracker in list(_trackers.items()):
        if not cmds.objExists(shape) or not cmds.objExists(tracker["source"]):
            continue
        cache = _cache.get(shape)
        if cache is None:
            continue
        ordered = _build_fill_order(_window_frames(shape), current)
        missing = [frame for frame in ordered if _frame_key(frame) not in cache["positions"]]
        if missing:
            _queue_fill(shape, missing, force=False)


def _position_changed(current, cached):
    if current is None or cached is None:
        return current != cached
    return any(abs(float(current[index]) - float(cached[index])) >= POSITION_TOLERANCE for index in range(3))


def _check_positions():
    global _check_job
    _check_job = None
    current_frame = _current_frame()
    for shape, tracker in list(_trackers.items()):
        if not cmds.objExists(shape) or not cmds.objExists(tracker["source"]):
            continue
        current_world, _camera = _positions_at(
            tracker["source"], _camera_transforms.get(shape), None
        )
        cached = (_cache.get(shape) or {}).get("positions", {}).get(_frame_key(current_frame))
        if _position_changed(current_world, cached):
            _queue_source_dirty(shape)


def _schedule_check():
    global _check_job
    if _check_job is not None or not _trackers:
        return
    try:
        _check_job = cmds.scriptJob(event=["idle", _check_positions], runOnce=True)
    except Exception:
        _check_job = None


def _has_upstream_animation(node):
    selection = om.MSelectionList()
    try:
        selection.add(node)
        dag_path = selection.getDagPath(0)
        iterator = om.MItDependencyGraph(
            dag_path.node(),
            om.MFn.kAnimCurve,
            om.MItDependencyGraph.kUpstream,
            om.MItDependencyGraph.kDepthFirst,
            om.MItDependencyGraph.kNodeLevel,
        )
        if not iterator.isDone():
            return True
    except Exception:
        pass
    try:
        connections = cmds.listConnections(node, source=True, destination=False) or []
        for connection in connections:
            if cmds.nodeType(connection) in {
                "parentConstraint", "pointConstraint", "orientConstraint",
                "scaleConstraint", "aimConstraint", "expression", "motionPath",
            }:
                return True
    except Exception:
        pass
    for parent in cmds.listRelatives(node, parent=True, fullPath=True) or []:
        if _has_upstream_animation(parent):
            return True
    return False


def _on_camera_changed(camera):
    global _camera_check_job
    _camera_dirty.add(camera)
    if _camera_check_job is None:
        try:
            _camera_check_job = cmds.scriptJob(
                event=["idle", _rebuild_dirty_cameras], runOnce=True
            )
        except Exception:
            _camera_check_job = None


def _rebuild_dirty_cameras():
    global _camera_dirty, _camera_check_job
    _camera_check_job = None
    dirty = set(_camera_dirty)
    _camera_dirty = set()
    for shape, camera in list(_camera_transforms.items()):
        if camera in dirty:
            mark_dirty(shape, refresh_keys=False)


def _clear_attr_jobs():
    global _attr_jobs, _camera_check_job, _camera_dirty
    for job in list(_attr_jobs):
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass
    _attr_jobs = []
    if _camera_check_job is not None:
        try:
            if cmds.scriptJob(exists=_camera_check_job):
                cmds.scriptJob(kill=_camera_check_job, force=True)
        except Exception:
            pass
    _camera_check_job = None
    _camera_dirty = set()


def _setup_attr_jobs():
    global _attr_jobs
    _clear_attr_jobs()
    cameras = {
        camera for camera in _camera_transforms.values()
        if camera and cmds.objExists(camera) and not _has_upstream_animation(camera)
    }
    for camera in cameras:
        try:
            cmds.getAttr(camera + ".worldMatrix[0]")
            _attr_jobs.append(cmds.scriptJob(
                attributeChange=[
                    camera + ".worldMatrix[0]",
                    lambda camera_name=camera: _on_camera_changed(camera_name),
                ],
                protected=True,
            ))
        except Exception:
            pass
    for shape, tracker in list(_trackers.items()):
        source = tracker["source"]
        if not cmds.objExists(source):
            continue
        try:
            cmds.getAttr(source + ".worldMatrix[0]")
            _attr_jobs.append(cmds.scriptJob(
                attributeChange=[
                    source + ".worldMatrix[0]",
                    lambda shape_name=shape: _on_source_changed(shape_name),
                ],
                protected=True,
            ))
        except Exception:
            pass


def _on_undo_redo():
    for shape in list(_trackers):
        mark_dirty(shape)


def _clear_jobs():
    global _jobs, _check_job
    _clear_attr_jobs()
    _clear_fill_queue()
    for job in list(_jobs):
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass
    _jobs = []
    if _check_job is not None:
        try:
            if cmds.scriptJob(exists=_check_job):
                cmds.scriptJob(kill=_check_job, force=True)
        except Exception:
            pass
    _check_job = None


def _setup_jobs():
    global _jobs
    _clear_jobs()
    if _scene_save_in_progress:
        return
    for event_name, callback in (
        ("timeChanged", _on_time_changed),
        ("Undo", _on_undo_redo),
        ("Redo", _on_undo_redo),
    ):
        try:
            _jobs.append(cmds.scriptJob(event=[event_name, callback], protected=True))
        except Exception:
            pass
    _setup_attr_jobs()


def _connect_camera(shape, camera, enabled):
    destination = shape + ".cameraWorldMatrix"
    try:
        existing = cmds.listConnections(
            destination, source=True, destination=False, plugs=True
        ) or []
        for plug in existing:
            try:
                cmds.disconnectAttr(plug, destination)
            except Exception:
                pass
    except Exception:
        pass
    if enabled and camera and cmds.objExists(camera):
        try:
            cmds.connectAttr(camera + ".worldMatrix[0]", destination, force=True)
        except Exception:
            pass
    try:
        cmds.setAttr(shape + ".cameraSpace", bool(enabled and camera))
    except Exception:
        pass


def _on_before_scene_save(*args):
    """Quiesce deferred cache work before Maya serializes custom trail nodes."""
    global _scene_save_in_progress, _scene_save_resume_scheduled
    _scene_save_in_progress = True
    _scene_save_resume_scheduled = False
    _clear_jobs()


def _resume_after_scene_save():
    global _scene_save_in_progress, _scene_save_resume_scheduled
    _scene_save_in_progress = False
    _scene_save_resume_scheduled = False
    if _trackers:
        _setup_jobs()


def _on_after_scene_save(*args):
    """Restore listeners only after the save callback has fully returned."""
    global _scene_save_resume_scheduled
    if not _scene_save_in_progress or _scene_save_resume_scheduled:
        return
    _scene_save_resume_scheduled = True
    if not _defer_low(_resume_after_scene_save):
        _resume_after_scene_save()


def _install_scene_callbacks():
    if _scene_callbacks:
        return
    try:
        _scene_callbacks.append(om.MSceneMessage.addCallback(
            om.MSceneMessage.kBeforeSave, _on_before_scene_save
        ))
        _scene_callbacks.append(om.MSceneMessage.addCallback(
            om.MSceneMessage.kAfterSave, _on_after_scene_save
        ))
    except Exception:
        _remove_scene_callbacks()


def _remove_scene_callbacks():
    while _scene_callbacks:
        callback = _scene_callbacks.pop()
        try:
            om.MMessage.removeCallback(callback)
        except Exception:
            pass


def is_scene_save_in_progress():
    """Return the save state for externally queued trail callbacks."""
    return bool(_scene_save_in_progress)


def initialize(entries, settings=None):
    """Register active trails and build each current visible window immediately."""
    global _trackers, _cache, _camera_transforms, _generation
    if _scene_save_in_progress:
        return
    settings = settings or {}
    _install_scene_callbacks()
    camera_space = bool(settings.get("camera_space", False))
    camera = _active_camera_transform() if camera_space else None
    _clear_jobs()
    _trackers = {}
    _cache = {}
    _camera_transforms = {}
    _generation += 1

    for entry in entries or []:
        shape = _canonical_node(entry.get("shape"))
        source = _canonical_node(entry.get("source"))
        if not shape or not source or not cmds.objExists(shape) or not cmds.objExists(source):
            continue
        tracker = {
            "container": entry.get("container"),
            "source": source,
            "start": float(entry.get("start", 1.0)),
            "end": float(entry.get("end", 120.0)),
            "increment": max(1, int(entry.get("increment", 1))),
            "density": max(1, int(entry.get("density", 1))),
            "display_range": max(0, int(settings.get("display_frame_range", DEFAULT_VISIBLE_RANGE))),
            "keyframes": [],
        }
        _trackers[shape] = tracker
        assigned_camera = camera if camera_space else None
        _camera_transforms[shape] = assigned_camera
        _connect_camera(shape, assigned_camera, camera_space)

    if _trackers:
        _setup_jobs()
    for shape in list(_trackers):
        _build_visible_cache(shape)
    try:
        cmds.refresh(currentView=True)
    except Exception:
        pass


def refresh(entries=None, settings=None):
    if entries is not None:
        initialize(entries, settings=settings)
        return
    for shape in list(_trackers):
        _build_visible_cache(shape)
    _setup_attr_jobs()
    try:
        cmds.refresh(currentView=True)
    except Exception:
        pass


def update_settings(settings):
    settings = settings or {}
    display_range = max(0, int(settings.get("display_frame_range", DEFAULT_VISIBLE_RANGE)))
    camera_space = bool(settings.get("camera_space", False))
    camera = _active_camera_transform() if camera_space else None
    current = _current_frame()
    for shape, tracker in list(_trackers.items()):
        old_increment = tracker.get("increment", 1)
        old_density = tracker.get("density", 1)
        tracker["display_range"] = display_range
        tracker["increment"] = max(1, int(settings.get("trail_increment", tracker.get("increment", 1))))
        tracker["density"] = max(1, int(settings.get("sample_density", tracker.get("density", 1))))
        old_camera = _camera_transforms.get(shape)
        new_camera = camera if camera_space else None
        _camera_transforms[shape] = new_camera
        _connect_camera(shape, new_camera, camera_space)
        if (
            old_camera != new_camera or
            old_increment != tracker["increment"] or
            old_density != tracker["density"]
        ):
            _build_visible_cache(shape)
            continue
        cache = _cache.get(shape)
        if cache is not None:
            cache["cameraSpace"] = bool(new_camera)
            cache["cameraTransform"] = new_camera
        frames = _window_frames(shape, bootstrap=False)
        missing = [frame for frame in frames if _frame_key(frame) not in (cache or {}).get("positions", {})]
        if missing:
            _queue_fill(shape, _build_fill_order(missing, current), force=False)
        _sync_cache(shape)
    _setup_attr_jobs()
    try:
        cmds.refresh(currentView=True)
    except Exception:
        pass


def shutdown(clear_cache=True):
    global _trackers, _cache, _camera_transforms, _generation
    _clear_jobs()
    _remove_scene_callbacks()
    _trackers = {}
    _camera_transforms = {}
    if clear_cache:
        _cache = {}
        _clear_parsed_draw_cache()
    _generation += 1


def remove(identifier):
    shape = _shape_for_identifier(identifier)
    if shape:
        _trackers.pop(shape, None)
        _cache.pop(shape, None)
        _camera_transforms.pop(shape, None)
        _fill_pending.pop(shape, None)
        _clear_parsed_draw_cache(shape)
    if _trackers:
        _setup_jobs()
    else:
        shutdown()


def job_ids():
    return list(_jobs) + list(_attr_jobs) + ([int(_check_job)] if _check_job is not None else [])


def cache_for(identifier):
    shape = _shape_for_identifier(identifier) or identifier
    return _cache.get(shape)


def trackers():
    return dict(_trackers)
