"""讓 ``python -m lawkit`` 可直接執行。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
