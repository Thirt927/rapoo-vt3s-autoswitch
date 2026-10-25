# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：产出单文件 exe（含托盘依赖）。

主 exe 用 ``console=False`` 构建——不分配控制台窗口，开机自启时零黑框。
交互命令（list/config/apply/setup/doctor/status）需要打印输出，但 Windows
下双击 exe 时系统会自动分配一个临时控制台（``ConsoleToAttach``），
因此交互命令仍可用。

常驻命令（``tray``/``daemon``）会被转发到 ``rapoo_autoswitch_console.exe``
（由 ``rapoo-autoswitch-console.spec`` 构建，``console=True``）去执行。

用法（在项目根目录）::

    pyinstaller --noconfirm packaging/rapoo-autoswitch.spec
    pyinstaller --noconfirm packaging/rapoo-autoswitch-console.spec

产出 ``dist/rapoo-autoswitch.exe`` + ``dist/rapoo_autoswitch_console.exe``，
用户无需安装 Python。
"""

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "packaging", "entry.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=[],
    # 这些模块都是运行时才 import 的，静态分析扫不到，必须显式声明
    hiddenimports=[
        "hid",  # cython-hidapi（原生库静态链接，无需额外 dll）
        "pystray",
        "pystray._win32",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "tkinter",  # 查看预设数据弹窗
        # 包内子模块（from . import X 形式在 PyInstaller 下偶发漏扫，显式补齐）
        "rapoo_autoswitch.autostart",
        "rapoo_autoswitch.daemon",
        "rapoo_autoswitch.doctor",
        "rapoo_autoswitch.device",
        "rapoo_autoswitch.profiles",
        "rapoo_autoswitch.protocol",
        "rapoo_autoswitch.config",
        "rapoo_autoswitch.transport",
        "rapoo_autoswitch.tray",
        "rapoo_autoswitch.cli",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="rapoo-autoswitch",
    debug=False,
    strip=False,
    upx=False,  # UPX 常被杀软误报，得不偿失
    console=False,  # 不分配控制台：开机自启零黑框，交互命令时系统自动附加临时控制台
)
