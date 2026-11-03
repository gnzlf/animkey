import unittest
import maya.standalone
try:
    maya.standalone.initialize(name="python")
except RuntimeError:
    pass
import maya.cmds as cmds
from AnimKey.core.undo_transaction import UndoTransaction


class UndoTransactionTests(unittest.TestCase):
    def setUp(self):
        cmds.undoInfo(state=True)
        cmds.file(new=True, force=True)
        self.node = cmds.createNode("transform")
        cmds.flushUndo()

    def tearDown(self):
        cmds.undoInfo(state=True)

    def test_failure_rolls_back_only_own_changes(self):
        cmds.setAttr(self.node + ".tx", 4.0)
        with self.assertRaises(ValueError):
            with UndoTransaction("test failure", rollback_on_failure=True):
                cmds.setAttr(self.node + ".tx", 12.0)
                raise ValueError("forced")
        self.assertEqual(cmds.getAttr(self.node + ".tx"), 4.0)
        cmds.undo()
        self.assertEqual(cmds.getAttr(self.node + ".tx"), 0.0)

    def test_empty_failure_never_undoes_previous_user_action(self):
        cmds.setAttr(self.node + ".tx", 4.0)
        with self.assertRaises(ValueError):
            with UndoTransaction("empty", rollback_on_failure=True):
                raise ValueError("forced before mutation")
        self.assertEqual(cmds.getAttr(self.node + ".tx"), 4.0)

    def test_atomic_transaction_refuses_disabled_undo_before_mutation(self):
        cmds.undoInfo(state=False)
        with self.assertRaises(RuntimeError):
            with UndoTransaction("disabled", rollback_on_failure=True):
                cmds.setAttr(self.node + ".tx", 20.0)
        self.assertFalse(cmds.undoInfo(query=True, state=True))
        self.assertEqual(cmds.getAttr(self.node + ".tx"), 0.0)

    def test_ordinary_chunk_preserves_disabled_undo_preference(self):
        cmds.undoInfo(state=False)
        with UndoTransaction("ordinary"):
            cmds.setAttr(self.node + ".tx", 20.0)
        self.assertFalse(cmds.undoInfo(query=True, state=True))

    def test_atomic_transaction_refuses_named_outer_chunk(self):
        with UndoTransaction("user outer"):
            cmds.setAttr(self.node + ".tx", 4.0)
            with self.assertRaises(RuntimeError):
                with UndoTransaction("nested", rollback_on_failure=True):
                    cmds.setAttr(self.node + ".tx", 20.0)
            self.assertEqual(cmds.undoInfo(query=True, chunkName=True), "user outer")
        self.assertEqual(cmds.getAttr(self.node + ".tx"), 4.0)
        cmds.undo()
        self.assertEqual(cmds.getAttr(self.node + ".tx"), 0.0)


if __name__ == "__main__":
    unittest.main()
