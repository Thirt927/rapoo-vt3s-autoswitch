"""后台守护：鼠标一接入，就把本机预设下发下去。

采用轮询探测而不是系统设备通知，是为了在 Windows / Linux / macOS 上用同一份
代码，且不引入额外依赖。默认 3 秒一次，代价极低。
"""

from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional

from . import device as dev
from .config import load_config
from .profiles import apply, load

log = logging.getLogger("rapoo_autoswitch.daemon")


def apply_preset(session: dev.Session, preset: str) -> List[str]:
    """下发指定快照，返回写成功的寄存器键。"""
    snapshot = load(preset)
    return apply(session, snapshot)


def run(
    preset: Optional[str] = None,
    interval: Optional[float] = None,
    vendor_id: Optional[int] = None,
    once: bool = False,
    on_event: Optional[Callable[[str, object], None]] = None,
) -> None:
    """守护主循环。

    :param preset: 要下发的快照名；缺省取本机配置里的 ``preset``。
    :param interval: 探测间隔（秒）。
    :param once: 只做一轮探测与下发，便于调试。
    :param on_event: 事件回调 ``(事件名, 负载)``，事件名见下方。
    """
    config = load_config()
    preset = preset or config.get("preset")
    interval = interval if interval is not None else float(config.get("poll_interval_s", 3.0))
    vendor_id = vendor_id if vendor_id is not None else int(config.get("vendor_id", dev.p.VENDOR_ID))

    if not preset:
        raise RuntimeError(
            "本机还没有设置预设。请先执行：rapoo-autoswitch use <快照名>"
        )

    def emit(name: str, payload: object = None) -> None:
        if on_event:
            on_event(name, payload)

    log.info("守护启动：本机=%s，预设=%s，探测间隔=%.1fs", __import__("socket").gethostname(), preset, interval)
    attached: Optional[int] = None

    while True:
        try:
            found = dev.first_device(vendor_id)
        except Exception as exc:  # noqa: BLE001 - 枚举失败不应终止守护
            log.error("枚举设备失败：%s", exc)
            found = None

        if found and attached is None:
            log.info("检测到鼠标接入：%s", found)
            emit("attached", found)
            try:
                with dev.open_session(found) as session:
                    written = apply_preset(session, preset)
                log.info("已下发预设「%s」：%d 项寄存器", preset, len(written))
                emit("applied", (preset, written))
            except Exception as exc:  # noqa: BLE001 - 单次下发失败继续守护
                log.error("下发预设失败：%s", exc)
                emit("error", exc)
                attached = None  # 允许下一轮重试
                time.sleep(interval)
                continue
            attached = found.product_id
        elif not found and attached is not None:
            log.info("鼠标已断开")
            emit("detached", attached)
            attached = None

        if once:
            return
        time.sleep(interval)
