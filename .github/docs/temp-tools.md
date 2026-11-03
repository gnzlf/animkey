# Temporary tools

The independent rewrite follows the two technical specifications supplied on
20 October 2026. It does not execute, import or depend on the reference product.

## Temp Pivot

`AnimKey/buttons/tempPivot.py` owns a short-lived `dagContainer` and proxy
transform. Maya's row-vector matrix convention is used:

```
relative = originalWorld * inverse(originalProxyWorld)
resultWorld = relative * currentProxyWorld
```

The four modes are Standard, Last Object, Centered and World. Standard includes
the last selected transform in the targets; Last excludes it and can reuse its
previous anchor. Centered uses min/max transform translations rather than shape
bounds or averages. World retains its proxy frame on timeline changes.

Pivot and manipulator orientation memory belongs to the Python session and the
anchor's full name. Source pivots, hierarchy and ownership are not rewritten.
Proxy nodes are marked not to serialize and are deleted when the session ends.
Manual proxy removal retires the session at idle, outside Maya's deletion
callback. Scene changes invalidate all pending callbacks.

Viewport input suppresses undo recording of the disposable proxy. Committing
the gesture records target channel/key changes in one owned transaction. The
programmatic equivalent is `with tempPivot.gesture() as proxy: ...`. Undo/Redo
rebase the relative cache without applying another delta. Timeline and Graph
Editor connections continue to show the target animation while the proxy is
selected, and owned UI connections are restored on exit.

## Temp Controls

`tempControls.py` implements persistent scene systems; `tempControlsUI.py`
provides a creation dialog and a separate scene panel. `core/temp_tools.py`
contains shared evaluated transforms, stable node identity and world solves.

Sources, controls, receivers, drivers, owned nodes and original input nodes are
connected by messages. Renaming, namespaces and save/reopen preserve identity.
Original rig inputs are kept rather than deleting their curves or layers.
Simple, Group, two-joint RP-IK Aim with pole/twist and FK orientation chains are
separate construction paths. Aim has an editable preview/confirmation stage.

World, Object (source parent), Relative, Camera, Child and Grab/Release spaces
resolve independently for Position and Orientation. Group excludes Object and
Child; FK excludes Child. Child does not connect a driver back to its source.
Space translation, rotation and scale use native constraints; complete affine
driver matrices preserve inherited shear instead of discarding it between
parent and scale constraints.

Bake Keys preserves local curves where compatible; conversion otherwise uses
evaluated matrices. Bake Frames includes integer samples and fractional keys.
Dynamic expression motion without keys uses the playback interval. Evaluated
capture reads matrix plugs in time contexts rather than scrubbing the timeline.
Applying samples preserves the caller's time, selection, auto-key and layer
selection. Bakes use an override layer with weight gates outside the processed
interval, leaving the original input animation available.

Toggle keeps the system. Revert restores rig inputs when disabling and the
saved temporary animation when enabling. Clear removes only the selected
systems; foreign children are detached with their sampled world animation.
Failed creation and bake operations roll back their owned transaction.

Grab/Release uses stepped constraint weights and accumulated matrix offsets
between driver frames. Transitions have indexed message connections at
`frame + 1000000`; nudge rebuilds both the indexed links and animation.
Normalization compensates control samples, and Y Up stores its reference frame.

Extra controls are members of their original system. Child and Parent use size
factors 0.8 and 1.2; driver links are redirected as needed. Removing an extra
transfers its motion. Group/FK membership and FK reordering retain world samples.
Editing a control pivot uses an auxiliary locator and compensates the control
frame and its driver. Appearance memory is independent from scene ownership.

## Deliberate format and fidelity differences

- Scene metadata and JSON exports use AnimKey schema version 3, not the vendor's
  scene format or `.anb` transport. The new manager discovers version 3 systems;
  legacy version 2 systems must be baked/reverted with their original engine.
- The referenced NURBS resource files were not supplied. The thirteen shape
  choices use AnimKey geometry and the documented orientation/size conventions.
- Native world setters, Smart Euler and parts of the original shared framework
  are explicitly unresolved in the documents. This implementation uses Maya
  matrix solves, native keying and AnimKey transactions; it does not claim to
  reproduce those private algorithms or every original interpolation result.
- Qt surfaces and programmatic Maya behavior are tested independently. No
  pixel comparison or interactive comparison with the original was performed.

## Validation

The specification, ownership, preservation, lifecycle and Qt suites cover all
four systems, spaces, layers, bake/revert, fractional samples, graph identity,
rollback, foreign nodes, Grab/Release, extras, FK reordering, JSON round trips,
proxy removal, undo/redo, layout order and the independent button/menu routes.

Run them with Maya 2024's `mayapy`, `QT_QPA_PLATFORM=offscreen` and the repository
plus `.github/tests/maya` on `PYTHONPATH`. Production-scene integration tests
remain opt-in through their fixture environment variables.
