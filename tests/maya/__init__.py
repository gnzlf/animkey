"""Maya Standalone integration tests for AnimKey."""

try:
    from PySide2 import QtWidgets
except ImportError:
    try:
        from PySide6 import QtWidgets
    except ImportError:
        QtWidgets = None


MAYA_TEST_APP = None
if QtWidgets is not None:
    MAYA_TEST_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
