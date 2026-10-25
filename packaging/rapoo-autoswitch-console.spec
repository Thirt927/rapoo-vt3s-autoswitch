# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：产出控制台版 exe（交互命令用）。

主 exe（console=False，不分配控制台窗口，开机自启零黑框）在收到
``daemon``/``tray`` 时通过 ``-m rapoo_autoswitch ...`` 拉起本 exe
去跑实际业务，自身立即退出。

交互命令（list/config/apply/setup/doctor/status）仍由主 exe 直接处理，
不需要控制台版。

用法（在项目根目录）::

    pyinstaller --noconfirm packaging/rapoo-autoswitch-console.spec

产出 ``dist/rapoo_autoswitch_console.exe``。
"""

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "packaging", "entry.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=[],
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
    name="rapoo_autoswitch_console",
    debug=False,
    strip=False,
    upx=False,  # UPX 常被杀软误报，得不偿失
    console=True,  # 需要控制台显示交互输出
)
