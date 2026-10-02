"""配置目录与运行配置。

"按电脑切换"的关键就在这里：配置（快照目录 + 本机预设）落在**每台电脑各自**
的用户目录下，所以同一只鼠标插到不同电脑上，会各自拿到该电脑的预设。
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, Dict

from . import protocol as p

APP_NAME = "rapoo-autoswitch"

DEFAULT_CONFIG: Dict[str, Any] = {
    # 本机要下发的快照名
    "preset": None,
    # 守护进程探测设备接入的间隔（秒）
    "poll_interval_s": 3.0,
    "vendor_id": p.VENDOR_ID,
}

# 兼容测试与不便写用户目录的场景
_ENV_OVERRIDE = "RAPOO_AUTOSWITCH_HOME"


def config_dir() -> Path:
    """用户配置目录，必要时创建。"""
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        path = Path(override)
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        path = Path(base) / APP_NAME
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshots_dir() -> Path:
    path = config_dir() / "snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> Dict[str, Any]:
    path = config_path()
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_config(config: Dict[str, Any]) -> None:
    config_path().write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def set_preset(preset: str) -> Dict[str, Any]:
    """设置本机预设并落盘。"""
    config = load_config()
    config["preset"] = preset
    save_config(config)
    return config


def machine_name() -> str:
    """本机标识，仅用于日志展示。"""
    return socket.gethostname()
