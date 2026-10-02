"""PyInstaller 打包入口。

单独放一个脚本而不是直接指向 ``rapoo_autoswitch/__main__.py``：后者用的是相对
导入（``from .cli import main``），被 PyInstaller 当顶层脚本执行时会失败。
"""

from rapoo_autoswitch.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
