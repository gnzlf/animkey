"""Read shipped Python code objects without importing or executing AnimBot."""
import dis
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import types

ROOT = Path(r"C:\Users\ANIM-gonzalo\Documents\maya\scripts\animBot")
TARGETS = ("mirror/__hybrid__/mirror310.pyc", "mirror/__hybrid__/mirrorPose310.pyc",
           "rigSnapshot/__hybrid__/rigSnapshot310.pyc",
           "selectionTools/__hybrid__/snapshotOppositeControls310.pyc")
report = {"execution": "static-code-object-inspection-only", "artifacts": []}


def inspect_code(code, prefix=""):
    name = prefix + code.co_name
    entry = {"name": name, "arguments": list(code.co_varnames[:code.co_argcount]),
             "names": list(code.co_names), "strings": [value for value in code.co_consts
                                                        if isinstance(value, str) and len(value) < 400],
             "disassembly": dis.Bytecode(code).dis()}
    yield entry
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            yield from inspect_code(value, name + ".")


for relative in TARGETS:
    path = ROOT / relative
    payload = path.read_bytes()
    if payload[:4] != importlib.util.MAGIC_NUMBER:
        raise RuntimeError("Python bytecode version mismatch: " + str(path))
    code = marshal.loads(payload[16:])
    entries = list(inspect_code(code))
    report["artifacts"].append({"path": str(path), "sha256": hashlib.sha256(payload).hexdigest(),
                                 "functions": entries})
    print(relative, flush=True)
    for item in entries:
        print("  {}({})".format(item["name"], ", ".join(item["arguments"])), flush=True)

destination = os.environ["ANIMKEY_ANIMBOT_STATIC_REPORT"]
with open(destination, "w", encoding="utf-8") as stream:
    json.dump(report, stream, indent=2)
print("STATIC_REPORT=" + destination, flush=True)
