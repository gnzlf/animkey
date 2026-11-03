"""Back up and install the validated copy/paste files in the existing Maya package."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import py_compile
import shutil

ROOT = Path(__file__).resolve().parents[2]
INSTALL = Path.home() / "Documents/maya/AnimKey"
BACKUP = ROOT / "dist" / ("copy-paste-update-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
FILES = ("buttons/copyAnimation.py", "core/animation_curve_transfer.py", "core/toolbar.py",
         "core/settings.py", "mods/hotkeysMod.py", "buttons/flashbuttons.py")
report = {"installation": str(INSTALL), "backup": str(BACKUP), "files": []}
for relative in FILES:
    source = ROOT / "AnimKey" / relative
    target = INSTALL / relative
    if not source.is_file() or not target.is_file():
        raise RuntimeError("Missing expected source/installed file: " + relative)
    py_compile.compile(str(source), doraise=True)
for relative in FILES:
    source = ROOT / "AnimKey" / relative
    target = INSTALL / relative
    backup = BACKUP / relative
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)
    shutil.copy2(source, target)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    report["files"].append({"path": str(target), "beforeSha256": digest(backup),
                             "sourceSha256": digest(source), "installedSha256": digest(target)})
report["verified"] = all(item["sourceSha256"] == item["installedSha256"] for item in report["files"])
(ROOT / "dist/copy-paste-installation.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
