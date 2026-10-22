# Maya UI lifecycle correction

The reported Maya 2024 crashes consistently ended in a Qt5/shiboken access
violation during QWidget destruction under Maya's workspace-control manager.
The previous implementation could be reproduced crashing in a disposable Maya
GUI session after floating the dock, deleting/reopening it and refreshing it.
The same operations did not fail in a plain Qt/standalone preview, so those
previews alone are insufficient regression coverage.

The toolbar now keeps its widgets in Maya's own dock hierarchy. The additional
native acrylic tool window is disabled inside Maya; regular tool windows still
use their rounded dark native material. Native glass previews outside Maya
remain supported and use C++ parent ownership plus an owned cancellable timer.

Refresh is deferred until Maya's idle queue, with repeated requests coalesced.
Animations, previews and pan callbacks stop before the dock is rebuilt. Tool
cleanup schedules deletion without forcing Qt to destroy widgets while their
button/window event is still being dispatched. Destruction signals no longer
call Python cleanup against partially destroyed toolbar widgets.

Validation:

- Six focused UI lifecycle regression tests passed.
- Sixteen Draw and Settings tests passed.
- Sixty native standalone toolbar move/resize/pending-callback/deletion cycles
  passed, plus the existing Qt5 and Qt6 material/toolbar checks.
- A disposable Maya 2024 GUI completed twenty cycles across Settings, Recovery,
  Retimer, Animation Cleaner and Temp Controls, moving/resizing/closing their
  windows, floating/redocking the toolbar, externally deleting/reopening the
  dock and requesting repeated refreshes. No native faults occurred. The test
  animation retained its keys, current frame and selection.

Run `.github/tools/verify_maya_ui_lifecycle.py` only through a separately launched
Maya with its required disposable profile and source/output environment variables.
It never targets an existing Maya process or opens the animator's working scene.
