"""Read-only comparison of reference edit records after the native test undo."""
from collections import Counter
import json
from pathlib import Path
import maya.cmds as cmds

root = Path(__file__).resolve().parents[2]
test = json.loads((root / "dist/mirror-active-animbot-swivel.json").read_text(encoding="utf-8"))
backup = json.loads(Path(test["backupFile"]).read_text(encoding="utf-8"))
result = {"references": {}, "sceneSaved": False}
for ref, before in backup["referenceEdits"].items():
    after = cmds.referenceQuery(ref, editStrings=True) or []
    result["references"][ref] = {
        "beforeCount": len(before), "afterCount": len(after),
        "added": list((Counter(after) - Counter(before)).elements()),
        "removed": list((Counter(before) - Counter(after)).elements()),
        "sameEditsIgnoringOrder": Counter(before) == Counter(after)}
result["scalarDifferences"] = {
    plug: {"expected": value, "actual": cmds.getAttr(plug)}
    for plug, value in backup["scalars"].items() if cmds.getAttr(plug) != value}
(root / "dist/mirror-active-reference-verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print("ANIMKEY_REFERENCE_VERIFICATION_COMPLETE")
