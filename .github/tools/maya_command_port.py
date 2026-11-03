"""Submit a local validation/reload script to an existing Maya command port."""
import json
from pathlib import Path
import socket
import sys

path = str(Path(sys.argv[1]).resolve()).replace("\\", "/")
source = "import maya.utils; maya.utils.executeDeferred(lambda: exec(compile(open({0}, encoding='utf-8').read(), {0}, 'exec'), {{'__file__': {0}, '__name__': '__main__'}}))".format(repr(path))
command = "python({});\n".format(json.dumps(source))
with socket.create_connection(("127.0.0.1", 50007), timeout=5) as connection:
    connection.sendall(command.encode("utf-8"))
    print(connection.recv(4096).decode("utf-8", errors="replace"))
