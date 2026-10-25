"""Windows 开机自启：写入当前用户的 Run 键，无需管理员权限。"""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "rapoo-autoswitch"

MODES = ("daemon", "tray")
"""可自启的常驻方式：``daemon`` 无界面守护，``tray`` 托盘版（带电量图标）。"""


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("开机自启仅在 Windows 上可用")


def command(mode: str = "daemon") -> str:
    """开机要执行的命令行；优先用 pythonw.exe 以免弹出控制台窗口。"""
    if mode not in MODES:
        raise ValueError(f"不支持的自启方式：{mode}（可选 {'、'.join(MODES)}）")
    if getattr(sys, "frozen", False):
        # 免安装 exe：主 exe 是 console=False（不分配控制台，开机自启零黑框）。
        # 直接传 daemon/tray 子命令即可——主 exe 收到后会通过
        # _maybe_reexec_daemon_or_tray 转发到控制台版 exe（rapoo_autoswitch_console.exe）
        # 去跑实际业务，自身立即退出。交互命令（list/config/apply 等）仍走
        # 控制台版 exe，不受影响。
        return f'"{sys.executable}" {mode} --hidden'
    executable = Path(sys.executable)
    quiet = executable.with_name("pythonw.exe")
    launcher = quiet if quiet.exists() else executable
    return f'"{launcher}" -m rapoo_autoswitch {mode} --hidden'


def install(mode: str = "daemon") -> str:
    _require_windows()
    import winreg

    cmd = command(mode)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
    return cmd


def uninstall() -> None:
    _require_windows()
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass


def current() -> str:
    """已登记的自启命令；未登记则返回空串。"""
    _require_windows()
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return value
    except FileNotFoundError:
        return ""
