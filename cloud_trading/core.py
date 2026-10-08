"""Load the supplied source core without loading its app, wiring or .env."""
import os
from pathlib import Path
import sys

from .config import ROOT

source = Path(os.getenv("TRADING_ENGINE_SRC", str(ROOT / "vendor/runtime_core"))).resolve()
if not (source / "audit_agent_runtime/querying/query_engine.py").is_file():
    raise RuntimeError("缺少agent-runtime核心源码，请配置TRADING_ENGINE_SRC。")
sys.path.insert(0, str(source))
