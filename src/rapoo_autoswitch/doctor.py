"""环境自检：把"装不上 / 用不了"的常见原因一次查清，并给出可执行的修复建议。

检查项全部来自实际踩过的坑，其中最要命的是**含中文的安装路径**——它会破坏
editable 安装写出的 ``.pth``，表现为 Python 直接报 ``Fatal Python error`` 或
``ModuleNotFoundError``，而报错信息完全看不出跟路径有关。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from . import autostart, device as dev, profiles
from .config import load_config
from .transport import hidapi_available, import_error

OK = "[OK]"
WARN = "[WARN]"
BAD = "[FAIL]"
"""只用 ASCII 标记：✅/⚠️/❌ 不在 GBK 字符集里，输出被重定向时会抛
``UnicodeEncodeError``（真机踩过）。"""

Check = Tuple[str, str, str, str]
"""``(等级, 名称, 说明, 修复建议)``。"""


def _python() -> Check:
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    if version >= (3, 9):
        return OK, "Python", text, ""
    return BAD, "Python", f"{text}（需要 3.9+）", "装 Python 3.9 或更高版本"


def _install_path() -> Check:
    """安装路径必须全是 ASCII——中文路径会把 editable 安装的 .pth 写坏。"""
    if getattr(sys, "frozen", False):
        # 单文件 exe 自带运行环境，不经过 pip/.pth，不存在路径编码问题
        return OK, "运行方式", f"单文件 exe（{Path(sys.executable).name}）", ""
    where = Path(__file__).resolve()
    if str(where).isascii():
        return OK, "安装路径", str(where.parent.parent), ""
    return (
        BAD,
        "安装路径",
        f"含非 ASCII 字符：{where}",
        "把项目移到纯英文路径（如 D:\\rapoo），再执行 pip install -e D:\\rapoo",
    )


def _hid_backend() -> Check:
    if hidapi_available():
        return OK, "HID 后端", "hidapi 可用", ""
    detail = f"导入失败：{type(import_error()).__name__}" if import_error() else "未安装"
    return (
        BAD,
        "HID 后端",
        detail,
        'pip install hidapi；若装过 hid（apmorton/pyhidapi）先 pip uninstall -y hid',
    )


def _tray_deps() -> Check:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return (
            WARN,
            "托盘依赖",
            "未安装（只有 tray 需要，daemon 不需要）",
            'pip install -e ".[tray]"',
        )
    return OK, "托盘依赖", "pystray + Pillow 可用", ""


def _device() -> Check:
    if not hidapi_available():
        return WARN, "设备", "跳过（HID 后端不可用）", ""
    try:
        found = dev.enumerate_devices()
    except Exception as exc:  # noqa: BLE001
        return WARN, "设备", f"枚举失败：{exc}", ""
    if not found:
        return WARN, "设备", "没找到雷柏二代鼠标", "确认接收器/鼠标已插好"
    names = "、".join(str(d) for d in found)
    return OK, "设备", names, ""


def _ahub_running() -> bool:
    """官方 A-Hub 是否在跑——它会抢状态接口，导致读不到电量。"""
    if sys.platform != "win32":
        return False
    try:
        done = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, timeout=10,
        )
    except Exception:  # noqa: BLE001 - 查不到就当没在跑
        return False
    return b"A HUB" in done.stdout or b"AHUB" in done.stdout


def _ahub() -> Check:
    if _ahub_running():
        return (
            WARN,
            "官方 A-Hub",
            "正在运行",
            "退出它（托盘右键→退出），否则 status/电量读不到，且它可能改掉你的配置",
        )
    return OK, "官方 A-Hub", "未运行", ""


def _autostart_check() -> Check:
    if sys.platform != "win32":
        return WARN, "开机自启", "仅 Windows 支持", ""
    current = autostart.current()
    if current:
        return OK, "开机自启", current, ""
    return WARN, "开机自启", "未设置", "rapoo-autoswitch startup install --mode tray"


def _preset() -> Check:
    config = load_config()
    names = profiles.list_names()
    if not names:
        return WARN, "本机预设", "还没有任何快照", "rapoo-autoswitch setup"
    name = config.get("preset")
    if not name:
        return WARN, "本机预设", f"未设置（已有 {len(names)} 个快照）", "rapoo-autoswitch use <快照名>"
    if name not in names:
        return WARN, "本机预设", f"「{name}」但快照不存在", f"rapoo-autoswitch use <{'/'.join(names)}>"
    return OK, "本机预设", f"{name}（共 {len(names)} 个快照）", ""


CHECKS = (
    _python,
    _install_path,
    _hid_backend,
    _tray_deps,
    _device,
    _ahub,
    _autostart_check,
    _preset,
)


def run() -> int:
    """跑一遍自检并打印结果；有阻塞项返回 1。"""
    print("rapoo-autoswitch 自检")
    print()
    blocking = 0
    for check in CHECKS:
        level, name, detail, fix = check()
        print(f"{level:<7}{name:<10} {detail}")
        if fix:
            print(f"   → {fix}")
        if level == BAD:
            blocking += 1
    print()
    if blocking:
        print(f"发现 {blocking} 项阻塞问题，按上面的建议处理后重跑 doctor。")
    else:
        print("没有阻塞问题。下一步：rapoo-autoswitch setup")
    return 1 if blocking else 0
