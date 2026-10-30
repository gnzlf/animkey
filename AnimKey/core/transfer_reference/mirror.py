"""Animation transfer kernel adapted from the user-provided Animo v10.7.5 source.
Only Maya data operations are bundled; AnimKey owns UI, storage and dispatch.
See provenance.json for original source hashes and deliberate interface changes.
"""
import os
import re
import sys
import json
import time
import getpass
import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om2
try:
    import builtins
except ImportError:
    import __builtin__ as builtins
max = builtins.max
min = builtins.min
sum = builtins.sum
abs = builtins.abs
len = builtins.len
int = builtins.int
str = builtins.str
set = builtins.set
range = builtins.range
list = builtins.list
dict = builtins.dict
import maya.cmds as cmds

class MirrorPlane(object):
    YZ = [-1, 1, 1]
    XZ = [1, -1, 1]
    XY = [1, 1, -1]
MIRROR_PLANE = MirrorPlane.YZ
SNAPSHOT_FRAME = -10000
SNAPSHOT_PAUSE_SECONDS = 0.15
SNAPSHOT_SECOND_PAUSE_SECONDS = 0.6
SNAPSHOT_FINAL_PAUSE_SECONDS = 0.2
PROGRESS_DELAY_SECONDS = 0.3
VALID_NODE_TYPES = ['joint', 'transform']
MANUAL_PAIRS = {}
TABLE_BUILD_VERSION = 5
SIDE_PATTERNS = [('FootIkLf', 'FootIkRt'), ('FootIkRt', 'FootIkLf'), ('ArmIkLf', 'ArmIkRt'), ('ArmIkRt', 'ArmIkLf'), ('_Left_', '_Right_'), ('_left_', '_right_'), ('_LEFT_', '_RIGHT_'), ('_L_', '_R_'), ('_l_', '_r_'), ('_Lt_', '_Rt_'), ('_lt_', '_rt_'), ('_Lf_', '_Rf_'), ('_lf_', '_rf_'), ('Left', 'Right'), ('left', 'right'), ('LEFT', 'RIGHT'), ('L_', 'R_'), ('l_', 'r_'), ('Lt_', 'Rt_'), ('lt_', 'rt_'), ('Lf_', 'Rf_'), ('lf_', 'rf_'), ('Lf', 'Rt'), ('Rt', 'Lf'), ('lf', 'rt'), ('rt', 'lf'), ('_Left', '_Right'), ('_left', '_right'), ('_LEFT', '_RIGHT'), ('_L', '_R'), ('_l', '_r'), ('_Lt', '_Rt'), ('_lt', '_rt'), ('_Lf', '_Rf'), ('_lf', '_rf')]
SIDE_TOKEN_GROUPS = [('left', 'right'), ('lft', 'rgt'), ('lt', 'rt'), ('lf', 'rf'), ('lf', 'rt'), ('lhs', 'rhs'), ('l', 'r')]
SIDE_TOKEN_PARTNERS = {}
for (_leftToken, _rightToken) in SIDE_TOKEN_GROUPS:
    SIDE_TOKEN_PARTNERS.setdefault(_leftToken, set()).add(_rightToken)
    SIDE_TOKEN_PARTNERS.setdefault(_rightToken, set()).add(_leftToken)
_TOKEN_PATTERN = re.compile('[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\\d+')
try:
    _home = os.path.expanduser('~')
    _user_root = _home.split(os.sep + 'Documents')[0] if os.sep + 'Documents' in _home else _home
    _CUSTOM_RESET_JSON_PATH = os.path.join(_user_root, 'Documents', 'animTools', 'channelbox_attributes.json')
except Exception:
    _CUSTOM_RESET_JSON_PATH = None

def _warn(message):
    cmds.warning(message)

def _pushQuietScriptEditor():
    try:
        state = {'warnings': cmds.scriptEditorInfo(query=True, suppressWarnings=True), 'errors': cmds.scriptEditorInfo(query=True, suppressErrors=True)}
        cmds.scriptEditorInfo(suppressWarnings=True, suppressErrors=True)
        return state
    except Exception:
        return None

def _popQuietScriptEditor(state):
    if not state:
        return
    try:
        cmds.scriptEditorInfo(suppressWarnings=state['warnings'], suppressErrors=state['errors'])
    except Exception:
        pass

def _get_namespace_from_object(obj):
    short_name = obj.split('|')[-1]
    if ':' in short_name:
        return short_name.rsplit(':', 1)[0]
    return ''

def _get_namespaces_of(objects):
    namespaces = set()
    for obj in objects:
        namespaces.add(_get_namespace_from_object(obj))
    return list(namespaces)

def _find_all_nurbs_curve_transforms_in_namespace(namespace):
    all_transforms = cmds.ls(type='transform', long=True) or []
    result = []
    for transform in all_transforms:
        if _get_namespace_from_object(transform) != namespace:
            continue
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
        for shape in shapes:
            if cmds.nodeType(shape) == 'nurbsCurve':
                result.append(transform)
                break
    return result

def autoSelectAllCtrls(fromObjects):
    namespaces = _get_namespaces_of(fromObjects)
    allCurvesLong = []
    for ns in namespaces:
        allCurvesLong.extend(_find_all_nurbs_curve_transforms_in_namespace(ns))
    for obj in fromObjects:
        if obj not in allCurvesLong:
            allCurvesLong.append(obj)
    allCurvesLong = list(dict.fromkeys(allCurvesLong))
    if not allCurvesLong:
        return []
    shortNames = cmds.ls(allCurvesLong) or []
    return list(dict.fromkeys(shortNames))

def _loadCustomResetValues():
    if not _CUSTOM_RESET_JSON_PATH or not os.path.exists(_CUSTOM_RESET_JSON_PATH):
        return {}
    try:
        with open(_CUSTOM_RESET_JSON_PATH, 'r') as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _getAttributeDefaultValue(obj, attr):
    try:
        vals = cmds.attributeQuery(attr, node=obj, listDefault=True)
        if vals:
            return vals[0]
    except Exception:
        pass
    return 0.0

def _getKeyableUnlockedAttrs(obj):
    return cmds.listAttr(obj, keyable=True, unlocked=True) or []

def _captureAttrValues(objs):
    values = {}
    for obj in objs:
        for attr in _getKeyableUnlockedAttrs(obj):
            plug = obj + '.' + attr
            try:
                values[plug] = cmds.getAttr(plug)
            except (RuntimeError, TypeError, ValueError):
                pass
    return values

def _restoreAttrValues(values):
    for (plug, value) in values.items():
        try:
            cmds.setAttr(plug, value)
        except (RuntimeError, TypeError, ValueError):
            pass

def _mObjectFromName(name):
    sel = om2.MSelectionList()
    try:
        sel.add(name)
        return sel.getDependNode(0)
    except RuntimeError:
        return None

def _dagNodeName(mobject):
    try:
        return om2.MFnDagNode(mobject).fullPathName()
    except RuntimeError:
        return None

def _getAttrOwnerNodes(obj):
    node = _mObjectFromName(obj)
    if node is None or not node.hasFn(om2.MFn.kDagNode):
        return [obj]
    owners = [_dagNodeName(node) or obj]
    try:
        fn = om2.MFnDagNode(node)
        childCount = fn.childCount()
    except RuntimeError:
        return owners
    for i in range(childCount):
        try:
            childObj = fn.child(i)
            childName = _dagNodeName(childObj)
        except RuntimeError:
            continue
        if not childName:
            continue
        if childObj.hasFn(om2.MFn.kShape):
            owners.append(childName)
            continue
        try:
            grandChildCount = om2.MFnDagNode(childObj).childCount()
        except RuntimeError:
            grandChildCount = 0
        if not grandChildCount:
            owners.append(childName)
    return owners

def _fastNodeType(name):
    node = _mObjectFromName(name)
    if node is None:
        return None
    try:
        return om2.MFnDependencyNode(node).typeName
    except RuntimeError:
        return None

def _pairOwnerNodes(srcObj, destObj):
    srcOwners = _getAttrOwnerNodes(srcObj)
    destOwners = _getAttrOwnerNodes(destObj)
    srcByType = {}
    for owner in srcOwners:
        nodeType = _fastNodeType(owner)
        if nodeType is None:
            continue
        srcByType.setdefault(nodeType, []).append(owner)
    destByType = {}
    for owner in destOwners:
        nodeType = _fastNodeType(owner)
        if nodeType is None:
            continue
        destByType.setdefault(nodeType, []).append(owner)
    pairs = []
    for (nodeType, srcList) in srcByType.items():
        destList = destByType.get(nodeType)
        if not destList:
            continue
        for (i, srcOwner) in enumerate(srcList):
            if i >= len(destList):
                break
            pairs.append((srcOwner, destList[i]))
    return pairs

def _ownerControlMap(partnerOf):
    mapping = {}
    for control in partnerOf:
        for owner in _getAttrOwnerNodes(control):
            mapping[owner] = control
    return mapping

def resetAllChannels(objects):
    customData = _loadCustomResetValues()
    resetCount = 0
    for obj in objects:
        try:
            if not cmds.objExists(obj):
                continue
            shortName = obj.split('|')[-1]
            nodeCustom = customData.get(obj) or customData.get(shortName)
            if nodeCustom:
                attrsToReset = list(nodeCustom.keys())
            else:
                attrsToReset = _getKeyableUnlockedAttrs(obj)
            for attr in attrsToReset:
                plug = obj + '.' + attr
                try:
                    if not cmds.attributeQuery(attr, node=obj, exists=True):
                        continue
                    if cmds.getAttr(plug, lock=True):
                        continue
                    if nodeCustom and attr in nodeCustom:
                        resetValue = nodeCustom[attr]
                    else:
                        resetValue = _getAttributeDefaultValue(obj, attr)
                    if not isinstance(resetValue, (int, float)):
                        continue
                    animCurves = cmds.listConnections(plug, source=True, destination=False, type='animCurve') or []
                    if animCurves:
                        t = cmds.currentTime(query=True)
                        cmds.setKeyframe(plug, time=(t, t), value=resetValue)
                        cmds.keyTangent(plug, time=(t, t), itt='auto', ott='auto')
                        resetCount += 1
                        continue
                    otherConnections = cmds.listConnections(plug, source=True, destination=False)
                    if otherConnections:
                        continue
                    cmds.setAttr(plug, resetValue)
                    resetCount += 1
                except (RuntimeError, TypeError, ValueError):
                    continue
        except (RuntimeError, TypeError, ValueError):
            continue
    return resetCount

def cleanupSnapshotFrameKeys(objects):
    liveObjects = [obj for obj in objects or [] if cmds.objExists(obj)]
    if not liveObjects:
        return
    try:
        cmds.cutKey(liveObjects, time=(SNAPSHOT_FRAME, SNAPSHOT_FRAME), clear=True)
    except (RuntimeError, TypeError):
        pass

def splitNamespaceAndPath(name):
    (path, _, base) = name.rpartition('|')
    (ns, _, short) = base.rpartition(':')
    prefix = (path + '|' if path else '') + (ns + ':' if ns else '')
    return (prefix, short)

def findPartnerName(obj, candidateSet):
    (prefix, short) = splitNamespaceAndPath(obj)
    for (left, right) in SIDE_PATTERNS:
        if left in short:
            candidate = prefix + short.replace(left, right, 1)
            if candidate != obj and candidate in candidateSet:
                return candidate
        elif right in short:
            candidate = prefix + short.replace(right, left, 1)
            if candidate != obj and candidate in candidateSet:
                return candidate
    return None

def _tokenizeWithSpans(name):
    return [(m.start(), m.end(), m.group(0)) for m in _TOKEN_PATTERN.finditer(name)]

def _matchTokenCase(sample, word):
    if sample.isupper():
        return word.upper()
    if sample[:1].isupper():
        return word.capitalize()
    return word.lower()

def findPartnerNameByTokens(obj, candidateSet):
    (prefix, short) = splitNamespaceAndPath(obj)
    for (start, end, text) in _tokenizeWithSpans(short):
        opposites = SIDE_TOKEN_PARTNERS.get(text.lower())
        if not opposites:
            continue
        for opposite in opposites:
            replacement = _matchTokenCase(text, opposite)
            candidateShort = short[:start] + replacement + short[end:]
            candidate = prefix + candidateShort
            if candidate != obj and candidate in candidateSet:
                return candidate
    return None

def _worldPos(obj):
    try:
        return cmds.xform(obj, query=True, worldSpace=True, translation=True)
    except Exception:
        return None

def _mirroredPoint(pos, mirrorPlane):
    return [pos[0] * mirrorPlane[0], pos[1] * mirrorPlane[1], pos[2] * mirrorPlane[2]]

def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5

def geometricFallbackPairs(objects, mirrorPlane):
    positions = {}
    for obj in objects:
        pos = _worldPos(obj)
        if pos:
            positions[obj] = pos
    names = list(positions.keys())
    used = set()
    pairs = {}

    def nearestMirrorMatch(obj):
        pos = positions[obj]
        target = _mirroredPoint(pos, mirrorPlane)
        moveDist = _dist(pos, target)
        if moveDist < 1e-05:
            return None
        best = None
        bestDist = None
        for other in names:
            if other == obj:
                continue
            d = _dist(positions[other], target)
            if bestDist is None or d < bestDist:
                bestDist = d
                best = other
        if best is None:
            return None
        threshold = max(moveDist * 0.05, 0.0001)
        if bestDist <= threshold:
            return best
        return None
    for obj in names:
        if obj in used:
            continue
        match = nearestMirrorMatch(obj)
        if not match or match in used:
            continue
        reciprocal = nearestMirrorMatch(match)
        if reciprocal == obj:
            pairs[obj] = match
            pairs[match] = obj
            used.add(obj)
            used.add(match)
    return pairs

def buildPairs(objects):
    objSet = set(objects)
    matched = set()
    partners = {}
    for (a, b) in MANUAL_PAIRS.items():
        if a in objSet and b in objSet:
            partners[a] = b
            partners[b] = a
            matched.add(a)
            matched.add(b)
    for obj in objects:
        if obj in matched:
            continue
        candidate = findPartnerName(obj, objSet)
        if not candidate:
            candidate = findPartnerNameByTokens(obj, objSet)
        if candidate and candidate not in matched:
            partners[obj] = candidate
            partners[candidate] = obj
            matched.add(obj)
            matched.add(candidate)
    stillUnmatched = [obj for obj in objects if obj not in matched]
    if stillUnmatched:
        geoPairs = geometricFallbackPairs(stillUnmatched, MIRROR_PLANE)
        for (obj, candidate) in geoPairs.items():
            if obj in matched:
                continue
            partners[obj] = candidate
            partners[candidate] = obj
            matched.add(obj)
            matched.add(candidate)
    for obj in objects:
        partners.setdefault(obj, None)
    return partners

def axisWorldPosition(obj, axis):
    transform1 = cmds.createNode('transform')
    try:
        (transform1,) = cmds.parent(transform1, obj, r=True)
        cmds.setAttr(transform1 + '.t', *axis)
        cmds.setAttr(transform1 + '.r', 0, 0, 0)
        cmds.setAttr(transform1 + '.s', 1, 1, 1)
        return cmds.xform(transform1, q=True, ws=True, piv=True)
    finally:
        cmds.delete(transform1)

def maxIndex(numbers):
    m = 0
    result = 0
    for i in numbers:
        v = abs(float(i))
        if v > m:
            m = v
            result = numbers.index(i)
    return result

def isAxisMirrored(srcObj, dstObj, axis, mirrorPlane):
    old1 = cmds.xform(srcObj, q=True, ws=True, piv=True)
    old2 = cmds.xform(dstObj, q=True, ws=True, piv=True)
    new1 = axisWorldPosition(srcObj, axis)
    new2 = axisWorldPosition(dstObj, axis)
    mp = mirrorPlane
    v1 = (mp[0] * (new1[0] - old1[0]), mp[1] * (new1[1] - old1[1]), mp[2] * (new1[2] - old1[2]))
    v2 = (new2[0] - old2[0], new2[1] - old2[1], new2[2] - old2[2])
    d = sum((p * q for (p, q) in zip(v1, v2)))
    return d < 0.0

def calculateMirrorAxisForCenter(obj, mirrorPlane):
    result = [1, 1, 1]
    transform0 = cmds.createNode('transform')
    try:
        (transform0,) = cmds.parent(transform0, obj, r=True)
        (transform0,) = cmds.parent(transform0, w=True)
        cmds.setAttr(transform0 + '.t', 0, 0, 0)
        t1 = axisWorldPosition(transform0, [1, 0, 0])
        t2 = axisWorldPosition(transform0, [0, 1, 0])
        t3 = axisWorldPosition(transform0, [0, 0, 1])
        t1 = ('%.3f' % t1[0], '%.3f' % t1[1], '%.3f' % t1[2])
        t2 = ('%.3f' % t2[0], '%.3f' % t2[1], '%.3f' % t2[2])
        t3 = ('%.3f' % t3[0], '%.3f' % t3[1], '%.3f' % t3[2])
        if mirrorPlane == MirrorPlane.YZ:
            x = [t1[0], t2[0], t3[0]]
            result[maxIndex(x)] = -1
        if mirrorPlane == MirrorPlane.XZ:
            y = [t1[1], t2[1], t3[1]]
            result[maxIndex(y)] = -1
        if mirrorPlane == MirrorPlane.XY:
            z = [t1[2], t2[2], t3[2]]
            result[maxIndex(z)] = -1
    finally:
        cmds.delete(transform0)
    return result

def calculateMirrorAxis(srcObj, dstObj, mirrorPlane):
    result = [1, 1, 1]
    dstObj = dstObj or srcObj
    if dstObj == srcObj or not cmds.objExists(dstObj):
        result = calculateMirrorAxisForCenter(srcObj, mirrorPlane)
    else:
        if isAxisMirrored(srcObj, dstObj, [1, 0, 0], mirrorPlane):
            result[0] = -1
        if isAxisMirrored(srcObj, dstObj, [0, 1, 0], mirrorPlane):
            result[1] = -1
        if isAxisMirrored(srcObj, dstObj, [0, 0, 1], mirrorPlane):
            result[2] = -1
    return result

def buildMirrorTableData(objects, pairingObjects=None):
    pairingObjects = pairingObjects if pairingObjects is not None else objects
    validObjects = [o for o in objects if cmds.nodeType(o) in VALID_NODE_TYPES]
    if not validObjects:
        return None
    validPairingObjects = [o for o in pairingObjects if cmds.nodeType(o) in VALID_NODE_TYPES]
    pairs = buildPairs(validPairingObjects)
    pairedCount = sum((1 for o in validObjects if pairs.get(o)))
    centerNames = sorted([o for o in validObjects if not pairs.get(o)])
    controlsData = {}
    failedControls = []
    for obj in validObjects:
        partner = pairs.get(obj)
        try:
            axis = calculateMirrorAxis(obj, partner, MIRROR_PLANE)
        except (RuntimeError, TypeError, ValueError):
            failedControls.append(obj)
            continue
        controlsData[obj] = {'axis': axis, 'partner': partner, 'version': TABLE_BUILD_VERSION}
    data = {'info': {'createdBy': getpass.getuser(), 'lastUpdated': str(time.time()).split('.')[0], 'pairedCount': pairedCount, 'unpairedCount': len(centerNames), 'unpairedControls': centerNames, 'failedControls': failedControls}, 'controls': controlsData}
    return data

def loadTable():
    if not os.path.exists(MIRROR_TABLE_PATH):
        return None
    try:
        with open(MIRROR_TABLE_PATH, 'r') as f:
            content = f.read().strip()
            return json.loads(content) if content else None
    except Exception:
        return None

def saveMirrorTableToDisk(newData):
    existing = loadTable() or {}
    mergedControls = existing.get('controls', {}) or {}
    mergedControls.update(newData.get('controls', {}))
    newData['controls'] = mergedControls
    output_dir = os.path.dirname(MIRROR_TABLE_PATH)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    with open(MIRROR_TABLE_PATH, 'w') as f:
        json.dump(newData, f, indent=2)

def tableCoversSelection(table, selection):
    if not table:
        return False
    tableControls = table.get('controls', {})
    if not tableControls:
        return False
    for obj in selection:
        entry = tableControls.get(obj)
        if not entry or 'partner' not in entry:
            return False
        if entry.get('version') != TABLE_BUILD_VERSION:
            return False
    return True

def getUncoveredControls(table, allCtrls):
    tableControls = (table or {}).get('controls', {}) or {}
    result = []
    for obj in allCtrls:
        entry = tableControls.get(obj)
        if not entry or 'partner' not in entry or entry.get('version') != TABLE_BUILD_VERSION:
            result.append(obj)
    return result

def resetPoseAndSaveMirrorTable(userSelection, existingTable=None):
    allCtrls = autoSelectAllCtrls(userSelection)
    if not allCtrls:
        _warn('Auto-select all controls failed for this rig. Please select all controls on the rig yourself, then run this script again so it can do a snapshot for a perfect mirror experience.')
        return None
    newCtrls = getUncoveredControls(existingTable, allCtrls)
    if not newCtrls:
        return existingTable
    originalTime = cmds.currentTime(query=True)
    originalSelection = cmds.ls(selection=True) or []
    originalGraphKeys = getSelectedGraphEditorKeys()
    cmds.select(newCtrls, replace=True)
    showSnapshotNotice('This snapshot only needs to run once - mirroring will be faster from here on out!')
    beginProgress('New Control Setup (mapping new controls only)', 100)
    updateProgress(5, 'Selecting new controls...')
    cmds.select(newCtrls, replace=True)
    cmds.waitCursor(state=True)
    cmds.refresh(suspend=True)
    quietState = _pushQuietScriptEditor()
    tableData = None
    resetCount = 0
    preResetValues = _captureAttrValues(newCtrls)
    try:
        updateProgress(15, 'Mapping setup: Resetting new channels & Snapshotting...')
        cmds.currentTime(SNAPSHOT_FRAME, edit=True)
        priorUndoState = cmds.undoInfo(q=True, state=True)
        cmds.undoInfo(state=True)
        cmds.undoInfo(openChunk=True, chunkName='mirrorPoseResetForSnapshot')
        try:
            try:
                cmds.cutKey(newCtrls, clear=True)
            except (RuntimeError, TypeError):
                pass
            resetCount = resetAllChannels(newCtrls)
            cmds.refresh(force=True)
            _smoothPause(SNAPSHOT_PAUSE_SECONDS, 20, 40, 'Mapping setup: Snapshotting...')
            cmds.refresh(force=True)
            _smoothPause(SNAPSHOT_SECOND_PAUSE_SECONDS, 40, 60, 'Mapping setup: Snapshotting...')
            cmds.refresh(force=True)
            _smoothPause(SNAPSHOT_FINAL_PAUSE_SECONDS, 60, 80, 'Mapping setup: Snapshotting...')
            updateProgress(88, 'Mapping setup: Calculating mirror axes & L/R pairs...')
            tableData = buildMirrorTableData(newCtrls, pairingObjects=allCtrls)
        finally:
            cmds.undoInfo(closeChunk=True)
            try:
                cmds.undo()
            except (RuntimeError, TypeError, ValueError):
                pass
            cmds.undoInfo(stateWithoutFlush=False)
            try:
                cleanupSnapshotFrameKeys(newCtrls)
                _restoreAttrValues(preResetValues)
            finally:
                cmds.undoInfo(stateWithoutFlush=True)
            try:
                cmds.undoInfo(state=priorUndoState)
            except (RuntimeError, TypeError, ValueError):
                pass
        updateProgress(95, 'Mapping setup: Restoring timeline...')
        cmds.currentTime(originalTime, edit=True)
    finally:
        cmds.refresh(suspend=False)
        cmds.refresh(force=True)
        _popQuietScriptEditor(quietState)
        cmds.waitCursor(state=False)
    updateProgress(100, 'Mapping setup: Saving settings...')
    cmds.select(originalSelection, replace=True)
    if originalGraphKeys:
        try:
            cmds.selectKey(clear=True)
            grouped = {}
            for k in originalGraphKeys:
                grouped.setdefault(k['curve'], set()).add(k['time'])
            for (curve, times) in grouped.items():
                for t in times:
                    cmds.selectKey(curve, time=(t, t), keyframe=True, add=True)
        except (RuntimeError, TypeError, ValueError):
            pass
    if not tableData or not tableData.get('controls'):
        endProgress()
        _warn('Mirror table build failed - no valid controls were found.')
        return None
    failedControls = tableData.get('info', {}).get('failedControls') or []
    if failedControls:
        preview = ', '.join(failedControls[:5])
        if len(failedControls) > 5:
            preview += ', +{0} more'.format(len(failedControls) - 5)
        _warn('Mirror Launcher: {0} control(s) could not be mapped and were skipped ({1}). Mirroring will still work on every other control.'.format(len(failedControls), preview))
    saveMirrorTableToDisk(tableData)
    endProgress()
    return loadTable()

def _isNumeric(v):
    return isinstance(v, (int, float))

def isAttrMirrored(attr, mirrorAxis):
    if mirrorAxis == [1, 1, 1]:
        return False
    axisIndex = {'X': 0, 'Y': 1, 'Z': 2}
    idx = axisIndex.get(attr[-1])
    if idx is None:
        return False
    if attr.startswith('translate'):
        return mirrorAxis[idx] == -1
    if attr.startswith('rotate'):
        return mirrorAxis[idx] == 1
    return False

def formatValue(attr, value, mirrorAxis):
    if isAttrMirrored(attr, mirrorAxis):
        return value * -1
    return value

def mirrorAxisFor(obj, dest, tableObjects):
    return tableObjects.get(obj, {}).get('axis') or tableObjects.get(dest, {}).get('axis') or [1, 1, 1]

def _resolveRealPlug(curve):
    node = _mObjectFromName(curve)
    if node is None:
        return None
    try:
        fn = om2.MFnDependencyNode(node)
        currentPlug = fn.findPlug('output', False)
    except RuntimeError:
        return None
    visitedNodes = set()
    for _ in range(10):
        try:
            destinations = currentPlug.destinations()
        except RuntimeError:
            return None
        if not destinations:
            return None
        nextPlug = destinations[0]
        nextNode = nextPlug.node()
        try:
            nextFn = om2.MFnDependencyNode(nextNode)
        except RuntimeError:
            return None
        nextNodeName = nextFn.name()
        if nextNodeName in visitedNodes:
            return None
        visitedNodes.add(nextNodeName)
        try:
            nextAttr = nextPlug.partialName(useLongNames=True)
        except RuntimeError:
            return None
        nodeType = nextFn.typeName
        if nodeType.startswith('animBlendNode'):
            if nextAttr.startswith('inputA'):
                suffix = nextAttr[len('inputA'):]
            elif nextAttr.startswith('inputB'):
                suffix = nextAttr[len('inputB'):]
            else:
                suffix = ''
            try:
                currentPlug = nextFn.findPlug('output' + suffix, False)
            except RuntimeError:
                return None
            continue
        fullNodeName = _dagNodeName(nextNode) if nextNode.hasFn(om2.MFn.kDagNode) else nextNodeName
        return fullNodeName + '.' + nextAttr
    return None

def getSelectedGraphEditorKeys():
    selectedCurves = cmds.keyframe(q=True, selected=True, name=True)
    if not selectedCurves:
        return []
    keysData = []
    for curve in selectedCurves:
        try:
            realPlug = _resolveRealPlug(curve)
            if not realPlug:
                continue
            (obj, attr) = realPlug.split('.', 1)
            times = cmds.keyframe(curve, q=True, selected=True, timeChange=True) or []
            for t in times:
                keysData.append({'object': obj, 'attr': attr, 'time': t, 'curve': curve})
        except Exception:
            continue
    return keysData

def getSelectedTimeRange():
    try:
        playBackSlider = mel.eval('$mirrorPose_tmp=$gPlayBackSlider')
        timeRange = cmds.timeControl(playBackSlider, query=True, rangeArray=True)
        if timeRange and len(timeRange) >= 2:
            startRange = int(timeRange[0])
            endRange = int(timeRange[1] - 1)
            if endRange > startRange:
                return (startRange, endRange)
    except Exception:
        pass
    return (None, None)

def _getRootAnimLayer():
    try:
        return cmds.animLayer(query=True, root=True)
    except (RuntimeError, TypeError, ValueError):
        return None

def _getCurrentAnimLayer(rootLayer):
    if not rootLayer:
        return None
    try:
        selectedLayers = cmds.treeView('AnimLayerTabanimLayerEditor', query=True, selectItem=True) or []
    except (RuntimeError, TypeError, ValueError):
        selectedLayers = []
    return selectedLayers[0] if selectedLayers else rootLayer

def _findCurveForPlugOnLayer(plug, layer):
    try:
        curve = cmds.animLayer(layer, query=True, findCurveForPlug=plug)
    except (RuntimeError, TypeError, ValueError):
        return None
    if not curve:
        return None
    return curve[0] if isinstance(curve, list) else curve

def _getOverrideCurvesForPlug(plug, rootLayer, allLayers):
    overrides = set()
    for layer in allLayers:
        if layer == rootLayer:
            continue
        curve = _findCurveForPlugOnLayer(plug, layer)
        if curve:
            overrides.add(curve)
    return overrides

def _findAllCurvesForPlugAcrossLayers(plug, rootLayer):
    """Return {layer: curve} for every animation layer, including the base
    layer, that has a curve keying this plug."""
    result = {}
    for layer in cmds.ls(type='animLayer') or []:
        curve = _findCurveForPlugOnLayer(plug, layer)
        if curve:
            result[layer] = curve
    return result

def _resolveCurveWithKeysInRange(plug, startRange, endRange):
    """Pick the animation curve backing this plug that actually has keys in
    the given range. Checks every animation layer directly via
    animLayer(findCurveForPlug=...) rather than relying on which layer the
    Anim Layer Editor UI happens to show as selected, so it finds the right
    curve even when that panel is closed or a different layer is active.
    Preference order when several layers have keys in range: the currently
    selected layer, then the base layer, then any other layer."""
    rootLayer = _getRootAnimLayer()
    if not rootLayer:
        conns = cmds.listConnections(plug, source=True, destination=False, type='animCurve', skipConversionNodes=True) or []
        return conns[0] if conns else None
    curvesByLayer = _findAllCurvesForPlugAcrossLayers(plug, rootLayer)
    if not curvesByLayer:
        allLayers = cmds.ls(type='animLayer') or []
        overrides = _getOverrideCurvesForPlug(plug, rootLayer, allLayers)
        conns = cmds.listConnections(plug, source=True, destination=False, type='animCurve', skipConversionNodes=True) or []
        for c in conns:
            if c not in overrides:
                return c
        return None
    currentLayer = _getCurrentAnimLayer(rootLayer)
    ordered = []
    if currentLayer in curvesByLayer:
        ordered.append(curvesByLayer[currentLayer])
    if rootLayer in curvesByLayer and curvesByLayer[rootLayer] not in ordered:
        ordered.append(curvesByLayer[rootLayer])
    for curve in curvesByLayer.values():
        if curve not in ordered:
            ordered.append(curve)
    for curve in ordered:
        try:
            keyCount = cmds.keyframe(curve, q=True, time=(startRange, endRange), keyframeCount=True)
        except (RuntimeError, TypeError, ValueError):
            keyCount = 0
        if keyCount:
            return curve
    return ordered[0]

def _plugHasAnimation(plug):
    """True if this plug is driven by a keyframe curve, either directly or
    through an animation layer's blend node."""
    try:
        connections = cmds.listConnections(plug, source=True, destination=False, skipConversionNodes=True) or []
    except (RuntimeError, TypeError, ValueError):
        return False
    for node in connections:
        try:
            nodeType = cmds.nodeType(node)
        except (RuntimeError, TypeError, ValueError):
            continue
        if nodeType.startswith('animCurve') or nodeType.startswith('animBlendNode'):
            return True
    return False

def _restoreGraphEditorSelection(originalObjects, originalGraphKeys):
    """Restore the object selection and exact Graph Editor key selection that
    was active before mirroring ran."""
    if not originalGraphKeys:
        return
    try:
        cmds.select(originalObjects, replace=True)
    except (RuntimeError, TypeError, ValueError):
        pass
    grouped = {}
    order = []
    for k in originalGraphKeys:
        plug = k['object'] + '.' + k['attr']
        if plug not in grouped:
            grouped[plug] = []
            order.append(plug)
        grouped[plug].append(k['time'])
    try:
        cmds.selectKey(clear=True)
    except (RuntimeError, TypeError, ValueError):
        pass
    first = True
    for plug in order:
        timeRanges = [(t, t) for t in grouped[plug]]
        try:
            cmds.selectKey(plug, time=timeRanges, keyframe=True, replace=first, add=not first)
            first = False
        except (RuntimeError, TypeError, ValueError):
            pass

def mirrorGraphEditorKeys(graphKeys, partnerOf, tableObjects, ownerControlMap=None, attrFilter=None):
    ownerControlMap = ownerControlMap if ownerControlMap is not None else _ownerControlMap(partnerOf)
    mirroredKeys = 0
    touchedSrc = set()
    cancelled = False
    mirroredPlugRanges = []
    if attrFilter:
        graphKeys = [k for k in graphKeys if k['attr'] in attrFilter]
    grouped = {}
    groupedCurve = {}
    order = []
    for k in graphKeys:
        key = (k['object'], k['attr'])
        if key not in grouped:
            grouped[key] = []
            order.append(key)
            groupedCurve[key] = k.get('curve')
        grouped[key].append(k['time'])
    pairCache = {}
    workItems = []
    for (obj, attr) in order:
        times = grouped[obj, attr]
        if not times:
            continue
        control = ownerControlMap.get(obj)
        if control is None:
            continue
        dest = partnerOf[control]
        if control == obj:
            destOwner = dest
        else:
            pairKey = (control, dest)
            if pairKey not in pairCache:
                pairCache[pairKey] = _pairOwnerNodes(control, dest)
            destOwner = None
            for (srcOwner, pairedDestOwner) in pairCache[pairKey]:
                if srcOwner == obj:
                    destOwner = pairedDestOwner
                    break
            if destOwner is None:
                continue
        if not cmds.attributeQuery(attr, node=destOwner, exists=True):
            continue
        axis = mirrorAxisFor(control, dest, tableObjects)
        srcPlug = obj + '.' + attr
        srcCurve = groupedCurve.get((obj, attr))
        workItems.append((control, srcPlug, srcCurve, destOwner, attr, axis, min(times), max(times)))
    if not workItems:
        return (0, touchedSrc, cancelled)
    capturedCurves = []
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        for (control, srcPlug, srcCurve, destOwner, attr, axis, startRange, endRange) in workItems:
            dupCurve = None
            try:
                if srcCurve and cmds.objExists(srcCurve):
                    dupCurve = cmds.duplicate(srcCurve)[0]
                else:
                    srcConnections = cmds.listConnections(srcPlug, source=True, destination=False, skipConversionNodes=True) or []
                    if srcConnections:
                        dupCurve = cmds.duplicate(srcConnections[0])[0]
            except (RuntimeError, TypeError, ValueError):
                dupCurve = None
            capturedCurves.append(dupCurve)
    finally:
        cmds.undoInfo(stateWithoutFlush=True)
    try:
        cmds.waitCursor(state=True)
        for (i, (control, srcPlug, srcCurve, destOwner, attr, axis, startRange, endRange)) in enumerate(workItems):
            dupCurve = capturedCurves[i]
            if not dupCurve or not cmds.objExists(dupCurve):
                continue
            destPlug = destOwner + '.' + attr
            try:
                if cmds.getAttr(destPlug, lock=True):
                    continue
            except Exception:
                continue
            try:
                cmds.copyKey(dupCurve, time=(startRange, endRange))
                cmds.pasteKey(destPlug, option='replace')
                if isAttrMirrored(attr, axis):
                    cmds.scaleKey(destPlug, time=(startRange, endRange), valueScale=-1, valuePivot=0)
                mirroredKeys += 1
                touchedSrc.add(control)
                mirroredPlugRanges.append((srcPlug, startRange, endRange))
                mirroredPlugRanges.append((destPlug, startRange, endRange))
            except (RuntimeError, TypeError, ValueError):
                continue
    finally:
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            for dupCurve in capturedCurves:
                if dupCurve and cmds.objExists(dupCurve):
                    try:
                        cmds.delete(dupCurve)
                    except Exception:
                        pass
        finally:
            cmds.undoInfo(stateWithoutFlush=True)
        cmds.waitCursor(state=False)
    if mirroredPlugRanges:
        try:
            rangeGroups = {}
            rangeOrder = []
            for (plug, plugStart, plugEnd) in mirroredPlugRanges:
                key = (plugStart, plugEnd)
                if key not in rangeGroups:
                    rangeGroups[key] = []
                    rangeOrder.append(key)
                rangeGroups[key].append(plug)
            cmds.selectKey(clear=True)
            for (i, key) in enumerate(rangeOrder):
                (plugStart, plugEnd) = key
                cmds.selectKey(rangeGroups[key], time=(plugStart, plugEnd), keyframe=True, replace=i == 0, add=i != 0)
        except (RuntimeError, TypeError, ValueError):
            pass
    return (mirroredKeys, touchedSrc, cancelled)

def _isSimpleKeyableAttr(owner, attr):
    if '.' in attr or '[' in attr:
        return False
    try:
        if cmds.attributeQuery(attr, node=owner, multi=True):
            return False
    except Exception:
        return False
    return True

def mirrorTimeRange(objects, partnerOf, tableObjects, startRange, endRange, attrFilter=None):
    mirroredKeys = 0
    touchedSrc = set()
    cancelled = False
    plan = []
    for obj in objects:
        dest = partnerOf.get(obj, obj)
        axis = mirrorAxisFor(obj, dest, tableObjects)
        for (srcOwner, destOwner) in _pairOwnerNodes(obj, dest):
            srcAttrs = cmds.listAttr(srcOwner, keyable=True, unlocked=True) or []
            destAttrs = set(cmds.listAttr(destOwner, keyable=True, unlocked=True) or [])
            attrs = [a for a in srcAttrs if a in destAttrs and _isSimpleKeyableAttr(srcOwner, a) and _isSimpleKeyableAttr(destOwner, a)]
            if attrFilter:
                attrs = [a for a in attrs if a in attrFilter]
            if not attrs:
                continue
            plan.append((obj, srcOwner, destOwner, attrs, axis))
    if not plan:
        return (0, touchedSrc, cancelled)
    workItems = []
    for (obj, srcOwner, destOwner, attrs, axis) in plan:
        for attr in attrs:
            srcPlug = srcOwner + '.' + attr
            srcCurve = _resolveCurveWithKeysInRange(srcPlug, startRange, endRange)
            if not srcCurve:
                continue
            try:
                keyCount = cmds.keyframe(srcCurve, q=True, time=(startRange, endRange), keyframeCount=True)
            except (RuntimeError, ValueError):
                continue
            if not keyCount:
                continue
            workItems.append((obj, srcPlug, srcCurve, destOwner, attr, axis))
    if not workItems:
        return (0, touchedSrc, cancelled)
    capturedCurves = []
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        for (obj, srcPlug, srcCurve, destOwner, attr, axis) in workItems:
            dupCurve = None
            try:
                if srcCurve and cmds.objExists(srcCurve):
                    dupCurve = cmds.duplicate(srcCurve)[0]
            except (RuntimeError, TypeError, ValueError):
                dupCurve = None
            capturedCurves.append(dupCurve)
    finally:
        cmds.undoInfo(stateWithoutFlush=True)
    try:
        cmds.waitCursor(state=True)
        for (i, (obj, srcPlug, srcCurve, destOwner, attr, axis)) in enumerate(workItems):
            dupCurve = capturedCurves[i]
            if not dupCurve or not cmds.objExists(dupCurve):
                continue
            destPlug = destOwner + '.' + attr
            try:
                if cmds.getAttr(destPlug, lock=True):
                    continue
            except Exception:
                continue
            try:
                cmds.copyKey(dupCurve, time=(startRange, endRange))
                cmds.pasteKey(destPlug, option='replace')
                if isAttrMirrored(attr, axis):
                    cmds.scaleKey(destPlug, time=(startRange, endRange), valueScale=-1, valuePivot=0)
                mirroredKeys += 1
                touchedSrc.add(obj)
            except (RuntimeError, TypeError, ValueError):
                continue
    finally:
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            for dupCurve in capturedCurves:
                if dupCurve and cmds.objExists(dupCurve):
                    try:
                        cmds.delete(dupCurve)
                    except Exception:
                        pass
        finally:
            cmds.undoInfo(stateWithoutFlush=True)
        cmds.waitCursor(state=False)
    return (mirroredKeys, touchedSrc, cancelled)

def mirrorCurrentPose(objects, partnerOf, tableObjects, attrFilter=None):
    namesToSnapshot = set(objects)
    for obj in objects:
        namesToSnapshot.add(partnerOf.get(obj, obj))
    snapshot = {}
    for name in namesToSnapshot:
        try:
            attrs = cmds.listAttr(name, keyable=True, unlocked=True) or []
        except (RuntimeError, TypeError, ValueError):
            attrs = []
        if attrFilter:
            attrs = [a for a in attrs if a in attrFilter]
        values = {}
        for attr in attrs:
            plug = name + '.' + attr
            try:
                val = cmds.getAttr(plug)
            except (RuntimeError, TypeError, ValueError):
                continue
            if not _isNumeric(val):
                continue
            values[attr] = val
        snapshot[name] = values
    mirroredCount = 0
    skippedCount = 0
    for obj in objects:
        dest = partnerOf.get(obj, obj)
        srcValues = snapshot.get(obj, {})
        destValues = snapshot.get(dest, {})
        if not srcValues or not destValues:
            skippedCount += 1
            continue
        axis = mirrorAxisFor(obj, dest, tableObjects)
        wroteAny = False
        for attr in destValues:
            if attr not in srcValues:
                continue
            newValue = formatValue(attr, srcValues[attr], axis)
            plug = dest + '.' + attr
            try:
                if _plugHasAnimation(plug):
                    t = cmds.currentTime(query=True)
                    cmds.setKeyframe(plug, time=(t, t), value=newValue)
                    cmds.keyTangent(plug, time=(t, t), itt='auto', ott='auto')
                else:
                    cmds.setAttr(plug, newValue)
                wroteAny = True
            except (RuntimeError, TypeError, ValueError):
                pass
        if wroteAny:
            mirroredCount += 1
        else:
            skippedCount += 1
    return (mirroredCount, skippedCount)

def _findRotateOrderMismatches(objects, partnerOf):
    mismatches = []
    seen = set()
    for obj in objects:
        dest = partnerOf.get(obj, obj)
        if dest == obj:
            continue
        pairKey = frozenset((obj, dest))
        if pairKey in seen:
            continue
        seen.add(pairKey)
        try:
            if not (cmds.attributeQuery('rotateOrder', node=obj, exists=True) and cmds.attributeQuery('rotateOrder', node=dest, exists=True)):
                continue
            srcOrder = cmds.getAttr(obj + '.rotateOrder')
            destOrder = cmds.getAttr(dest + '.rotateOrder')
        except (RuntimeError, TypeError, ValueError):
            continue
        if srcOrder != destOrder:
            mismatches.append((obj, dest))
    return mismatches

def _getSelectedChannelBoxAttrs(objects=None):
    channelBox = None
    try:
        channelBox = mel.eval('$tempMirrorLauncherCbName = $gChannelBoxName')
    except (RuntimeError, TypeError, ValueError):
        channelBox = None
    if not channelBox:
        channelBox = 'mainChannelBox'
    try:
        if not cmds.channelBox(channelBox, exists=True):
            return None
    except (RuntimeError, TypeError, ValueError):
        return None
    attrs = []
    for flag in ('selectedMainAttributes', 'selectedShapeAttributes', 'selectedHistoryAttributes', 'selectedOutputAttributes'):
        try:
            result = cmds.channelBox(channelBox, query=True, **{flag: True})
        except (RuntimeError, TypeError, ValueError):
            result = None
        if result:
            attrs.extend(result)
    if not attrs:
        return None
    normalized = set()
    for attr in attrs:
        longName = None
        for obj in objects or []:
            try:
                if not cmds.attributeQuery(attr, node=obj, exists=True):
                    continue
                longName = cmds.attributeQuery(attr, node=obj, longName=True)
            except (RuntimeError, TypeError, ValueError):
                continue
            if longName:
                break
        normalized.add(longName if longName else attr)
    return normalized if normalized else None

def applyMirror(objects, tableObjects, timeRange=None, preCapturedGraphKeys=None, preCapturedChannelBoxAttrs=None):
    partnerOf = {}
    for obj in objects:
        storedPartner = tableObjects.get(obj, {}).get('partner')
        if storedPartner and cmds.objExists(storedPartner):
            partnerOf[obj] = storedPartner
        else:
            partnerOf[obj] = obj
    mismatches = _findRotateOrderMismatches(objects, partnerOf)
    if mismatches:
        names = sorted(set((o.split('|')[-1] for pair in mismatches for o in pair)))
        preview = ', '.join(names[:6])
        if len(names) > 6:
            preview += ', +{0} more'.format(len(names) - 6)
        choice = cmds.confirmDialog(title='Rotate Order Mismatch', message="These controls don't have a matching rotate order:\n{0}\n\nThis might result in weird mirroring. Continue?".format(preview), button=['Continue', 'Select Ctrls', 'Cancel'], defaultButton='Cancel', cancelButton='Cancel', dismissString='Cancel')
        if choice == 'Select Ctrls':
            mismatchedCtrls = sorted(set((o for pair in mismatches for o in pair)))
            try:
                cmds.select(mismatchedCtrls, replace=True)
            except (RuntimeError, TypeError, ValueError):
                pass
            return
        if choice != 'Continue':
            return
    touchedSrc = set()
    cmds.undoInfo(openChunk=True, chunkName='mirrorPoseApply')
    try:
        ownerControlMap = _ownerControlMap(partnerOf)
        channelBoxAttrs = preCapturedChannelBoxAttrs if preCapturedChannelBoxAttrs is not None else _getSelectedChannelBoxAttrs(objects)
        rawGraphKeys = preCapturedGraphKeys if preCapturedGraphKeys is not None else getSelectedGraphEditorKeys()
        explicitGraphKeys = [k for k in rawGraphKeys if k['object'] in ownerControlMap]
        if timeRange is not None:
            (startRange, endRange) = timeRange
        else:
            (startRange, endRange) = getSelectedTimeRange()
        hasTimeRange = startRange is not None and endRange is not None
        if hasTimeRange:
            graphKeys = []
        else:
            graphKeys = explicitGraphKeys
        if graphKeys:
            (mirroredKeys, touchedSrc, cancelled) = mirrorGraphEditorKeys(graphKeys, partnerOf, tableObjects, ownerControlMap=ownerControlMap, attrFilter=channelBoxAttrs)
            mode = 'graph_keys'
        elif hasTimeRange:
            (mirroredKeys, touchedSrc, cancelled) = mirrorTimeRange(objects, partnerOf, tableObjects, startRange, endRange, attrFilter=channelBoxAttrs)
            mode = 'range'
        else:
            cmds.waitCursor(state=True)
            try:
                (mirroredCount, skippedCount) = mirrorCurrentPose(objects, partnerOf, tableObjects, attrFilter=channelBoxAttrs)
            finally:
                cmds.waitCursor(state=False)
            cancelled = False
            mode = 'pose'
        dirtyObjs = set((partnerOf.get(src, src) for src in touchedSrc))
        if mode == 'pose':
            dirtyObjs.update(partnerOf.values())
        if dirtyObjs:
            try:
                cmds.dgdirty(list(dirtyObjs))
            except (RuntimeError, TypeError):
                pass
        if mode == 'graph_keys':
            _restoreGraphEditorSelection(objects, rawGraphKeys)
        elif dirtyObjs:
            try:
                cmds.select(list(dirtyObjs), replace=True)
            except (RuntimeError, TypeError):
                pass
    finally:
        cmds.undoInfo(closeChunk=True)
        pass
    cmds.refresh(force=True)
    if mode == 'graph_keys':
        if mirroredKeys == 0 and (not cancelled):
            _warn('No Graph Editor keys were mirrored. Make sure the keys you selected belong to controls with a verified partner.')
        elif cancelled:
            _warn('Mirroring stopped early (Esc) - mirrored {0} of the selected Graph Editor key(s) from {1} control(s) before stopping.'.format(mirroredKeys, len(touchedSrc)))
    elif mode == 'range':
        if mirroredKeys == 0 and (not cancelled):
            _warn('No keys were found in the highlighted timeline range for the selection.')
        elif cancelled:
            _warn('Mirroring stopped early (Esc) - mirrored {0} key(s) before stopping.'.format(mirroredKeys))
    elif mirroredCount == 0:
        _warn('No objects were mirrored. The selected control(s) may not have a verified partner in the table.')
MIRROR_TABLE_PATH = None
MIRROR_SETTINGS_DIR = None

def _quiet_interface(*args, **kwargs):
    return None

showSnapshotNotice = hideSnapshotNotice = beginProgress = updateProgress = endProgress = _smoothPause = _quiet_interface
def progressCancelled():
    return False
