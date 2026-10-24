import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps"))
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT))

try:
    import cli as ckit
    sys.modules["ckit"] = ckit
    import cli.moss_template
    sys.modules["ckit.moss_template"] = cli.moss_template
    import cli.mcp_server
    sys.modules["ckit.mcp_server"] = cli.mcp_server
    import cli.cli
    sys.modules["ckit.cli"] = cli.cli
except Exception:
    pass
