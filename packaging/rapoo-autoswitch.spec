# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：产出单文件 exe（含托盘依赖）。

用法（在项目根目录）::

    pyinstaller --noconfirm packaging/rapoo-autoswitch.spec

产出 ``dist/rapoo-autoswitch.exe``，用户无需安装 Python。
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
    console=True,  # 需要控制台显示 doctor/setup 输出
)
