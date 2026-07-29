"""双击启动 DAISY 图形界面。"""
import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_BASE, "Script"))

import Script_DAISY_GUI

raise SystemExit(Script_DAISY_GUI.main())
