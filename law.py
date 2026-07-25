#!/usr/bin/env python3
"""法規整理工具的執行檔（不需安裝，直接 ``python law.py`` 即可）。

範例：
    python law.py init
    python law.py import samples\\民法-節錄.txt
    python law.py serve
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lawkit.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
