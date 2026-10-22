"""Run the copy/paste checks without unrelated feature suites."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / ".github/tests/maya")]
names = ["test_copy_paste_scene_regressions", "test_copy_storage", "test_curve_clip_boundaries",
         "test_animation_curve_batch", "test_slider_copy_layers_regressions",
         "test_animation_tool_menus.AnimationToolMenuTests.test_copy_animation_menu_exposes_file_saves_and_loads"]
names += ["test_offset_copy_animo_regressions.OffsetAndCopyAnimoRegressionTests." + name for name in (
    "test_copy_uses_exact_selected_graph_keys", "test_graph_copy_replace_preserves_keys_outside_selected_span",
    "test_selected_graph_curve_identifies_nonpreferred_source_layer", "test_two_selected_rig_roots_receive_complete_assignments")]
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
sys.exit(0 if result.wasSuccessful() else 1)
