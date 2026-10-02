"""托盘常驻版：任务栏显示电量，右键切换配置。

面向 Windows，依赖 ``pystray`` + ``Pillow``（可选依赖）：

    pip install "rapoo-autoswitch[tray]"
    rapoo-autoswitch tray

与 ``daemon`` 的区别：托盘版是常驻 GUI，能看电量、能手动切配置；``daemon`` 是
无界面的守护进程，适合配开机自启。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

from . import device as dev
from . import profiles
from .config import load_config, set_preset

log = logging.getLogger("rapoo_autoswitch.tray")

POLL_S = 5.0
"""电量刷新间隔（秒）。"""


def _load_tray_libs():
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - 取决于是否装了可选依赖
        raise RuntimeError(
            '托盘模式需要额外依赖，请执行：pip install "rapoo-autoswitch[tray]"'
        ) from exc
    return pystray, Image, ImageDraw


class TrayApp:
    """托盘图标 + 右键菜单。"""

    def __init__(self, preset: Optional[str] = None, interval: float = POLL_S):
        pystray, Image, ImageDraw = _load_tray_libs()
        self._pystray = pystray
        self._Image = Image
        self._ImageDraw = ImageDraw

        config = load_config()
        self.preset = preset or config.get("preset")
        self.interval = interval
        self.vendor_id = int(config.get("vendor_id", dev.p.VENDOR_ID))

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._connected = False
        self._battery = 0
        self._charging = False
        self._note = "等待鼠标接入…"

        self.icon = pystray.Icon(
            "rapoo-autoswitch",
            icon=self._render_icon(),
            title="rapoo-autoswitch",
            menu=self._build_menu(),
        )

    # -- 菜单 -------------------------------------------------------------
    def _build_menu(self):
        pystray = self._pystray
        items = [
            pystray.MenuItem(lambda _: self._status_line(), None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]

        names = profiles.list_names()
        if names:
            for name in names:
                items.append(
                    pystray.MenuItem(
                        name,
                        self._make_switcher(name),
                        checked=lambda _item, n=name: n == self.preset,
                        radio=True,
                    )
                )
        else:
            items.append(pystray.MenuItem("（还没有快照）", None, enabled=False))

        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("立即重新下发", self._reapply, enabled=lambda _: bool(self.preset)),
            pystray.MenuItem("刷新电量", lambda: self._refresh_icon()),
            pystray.MenuItem("退出", self._quit),
        ]
        return pystray.Menu(*items)

    def _status_line(self) -> str:
        if not self._connected:
            return "未检测到鼠标"
        if not self.preset:
            return f"电量 {self._battery}% · 未设置预设"
        return f"电量 {self._battery}% · 预设：{self.preset}"

    def _make_switcher(self, name: str):
        def switch(_icon=None, _item=None):
            self.preset = name
            set_preset(name)
            self._note = f"已切到「{name}」"
            self._apply_now()
            self._refresh_icon()

        return switch

    def _reapply(self, _icon=None, _item=None) -> None:
        self._apply_now()
        self._refresh_icon()

    def _apply_now(self) -> None:
        if not self.preset:
            return
        try:
            found = dev.first_device(self.vendor_id)
        except Exception as exc:  # noqa: BLE001
            self._note = f"枚举失败：{exc}"
            return
        if found is None:
            self._note = "鼠标未连接"
            return
        try:
            with dev.open_session(found) as session:
                written = profiles.apply(session, profiles.load(self.preset))
            self._note = f"已下发 {len(written)} 项"
            log.info("已下发预设「%s」：%d 项寄存器", self.preset, len(written))
        except Exception as exc:  # noqa: BLE001
            self._note = f"下发失败：{exc}"
            log.error("下发预设失败：%s", exc)

    def _quit(self, _icon=None, _item=None) -> None:
        self._stop.set()
        self.icon.stop()

    # -- 图标 -------------------------------------------------------------
    def _render_icon(self):
        Image, ImageDraw = self._Image, self._ImageDraw
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        if not self._connected:
            color = (120, 120, 120)
        elif self._charging:
            color = (90, 170, 255)
        elif self._battery <= 20:
            color = (235, 80, 80)
        elif self._battery <= 50:
            color = (235, 180, 70)
        else:
            color = (90, 200, 130)

        draw.rounded_rectangle([4, 16, 52, 48], radius=7, outline=color, width=4)
        draw.rectangle([53, 25, 58, 39], fill=color)  # 正极

        span = 40  # 内腔宽度
        filled = int(span * max(0, min(self._battery, 100)) / 100)
        if filled > 0:
            draw.rounded_rectangle([9, 21, 9 + filled, 43], radius=3, fill=color)

        if self._charging:  # 闪电
            draw.polygon([(32, 20), (24, 34), (30, 34), (27, 45), (38, 30), (31, 30)], fill=(255, 255, 255))
        return image

    def _refresh_icon(self) -> None:
        self.icon.icon = self._render_icon()
        self.icon.title = f"rapoo-autoswitch — {self._status_line()}"

    # -- 后台轮询 ---------------------------------------------------------
    def _worker(self) -> None:
        attached: Optional[int] = None
        while not self._stop.is_set():
            try:
                found = dev.first_device(self.vendor_id)
            except Exception as exc:  # noqa: BLE001
                log.error("枚举设备失败：%s", exc)
                found = None

            if found is None:
                with self._lock:
                    changed = self._connected
                    self._connected = False
                    self._battery = 0
                    self._charging = False
                attached = None
                if changed:
                    self._refresh_icon()
            else:
                report = None
                try:
                    with dev.open_session(found) as session:
                        report = session.wait_status(timeout_s=1.5)
                        if attached is None and self.preset:
                            try:
                                written = profiles.apply(session, profiles.load(self.preset))
                                log.info("接入自动下发「%s」：%d 项", self.preset, len(written))
                            except Exception as exc:  # noqa: BLE001
                                log.error("自动下发「%s」失败：%s", self.preset, exc)
                                self._note = f"下发失败：{exc}"
                except Exception as exc:  # noqa: BLE001
                    log.error("读取状态失败：%s", exc)

                newly_attached = attached is None
                attached = found.product_id
                with self._lock:
                    self._connected = True
                    if report is not None:
                        self._battery = report.battery
                        self._charging = report.charging
                if newly_attached or report is not None:
                    self._refresh_icon()

            self._stop.wait(self.interval)

    # -- 入口 -------------------------------------------------------------
    def run(self) -> None:
        def setup(icon):
            icon.visible = True
            threading.Thread(target=self._worker, name="rapoo-poll", daemon=True).start()

        log.info("托盘启动，预设=%s", self.preset or "（未设置）")
        self.icon.run(setup=setup)
