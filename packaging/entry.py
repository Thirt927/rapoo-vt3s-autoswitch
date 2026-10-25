"""PyInstaller 打包入口。

单独放一个脚本而不是直接指向 ``rapoo_autoswitch/__main__.py``：后者用的是相对
导入（``from .cli import main``），被 PyInstaller 当顶层脚本执行时会失败。

显式导入所有子模块——PyInstaller 静态分析扫不到 ``from . import X`` 这种包相对
导入形式，不显式列出的子模块不会被打进 PYZ，运行时才 ImportError。
"""

import rapoo_autoswitch.autostart
import rapoo_autoswitch.daemon
import rapoo_autoswitch.device
import rapoo_autoswitch.doctor
import rapoo_autoswitch.profiles
import rapoo_autoswitch.protocol
import rapoo_autoswitch.transport
import rapoo_autoswitch.tray
import rapoo_autoswitch.config

from rapoo_autoswitch.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
