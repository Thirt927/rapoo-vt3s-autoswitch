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
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - 取决于是否装了可选依赖
        raise RuntimeError(
            '托盘模式需要额外依赖，请执行：pip install "rapoo-autoswitch[tray]"'
        ) from exc
    return pystray, Image, ImageDraw, ImageFont


class TrayApp:
    """托盘图标 + 右键菜单。"""

    def __init__(self, preset: Optional[str] = None, interval: float = POLL_S):
        pystray, Image, ImageDraw, ImageFont = _load_tray_libs()
        self._pystray = pystray
        self._Image = Image
        self._ImageDraw = ImageDraw
        self._ImageFont = ImageFont

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
        self._pending_apply = False

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
        bolt = " ⚡" if self._charging else ""
        if not self.preset:
            return f"电量 {self._battery}%{bolt} · 未设置预设"
        return f"电量 {self._battery}%{bolt} · 预设：{self.preset}"

    def _make_switcher(self, name: str):
        def switch(_icon=None, _item=None):
            self.preset = name
            set_preset(name)
            self._note = f"已切到「{name}」"
            self._request_apply()
            self._refresh_icon()

        return switch

    def _reapply(self, _icon=None, _item=None) -> None:
        self._request_apply()
        self._refresh_icon()

    def _request_apply(self) -> None:
        """菜单线程只登记"待下发"，真正的下发交给持有设备句柄的后台线程。

        设备句柄由后台线程独占持有，两个线程同时读写同一个 HID 通道会互相干扰。
        """
        with self._lock:
            self._pending_apply = True

    def _quit(self, _icon=None, _item=None) -> None:
        self._stop.set()
        self.icon.stop()

    # -- 图标 -------------------------------------------------------------
    ICON_SIZE = 64
    """画布边长。托盘会按实际尺寸缩放，这里留大一点，高 DPI 下也清楚。"""

    @staticmethod
    def _level_color(connected: bool, charging: bool, battery: int):
        """按连接/充电/电量取图标底色。

        底色统一压到较深的一档：白字对底色的对比度都 ≥ 4.5:1（WCAG AA）。
        亮色调（原来的浅绿/浅黄/浅蓝配白字只有 2.2~4.1:1）在 16×16 下会糊。
        """
        if not connected:
            return (113, 113, 122)  # #71717A  4.9:1
        if charging:
            return (29, 78, 216)    # #1D4ED8  6.7:1
        if battery <= 20:
            return (185, 28, 28)    # #B91C1C  6.5:1
        if battery <= 50:
            return (180, 83, 9)     # #B45309  5.0:1
        return (21, 128, 61)        # #15803D  5.0:1

    def _fit_font(self, text: str, max_w: int, max_h: int):
        """挑一个能把 ``text`` 塞进 ``max_w × max_h`` 的最大粗体字号。

        三位数（100）比两位数宽，字号不能写死——按实际边界自适应。
        """
        ImageFont = self._ImageFont
        for name in ("arialbd.ttf", "segoeuib.ttf", "arial.ttf"):
            for size in range(54, 10, -2):
                try:
                    font = ImageFont.truetype(name, size)
                except OSError:
                    break  # 系统里没有这个字体，换下一个
                left, top, right, bottom = font.getbbox(text)
                if right - left <= max_w and bottom - top <= max_h:
                    return font
        return ImageFont.load_default()

    def _render_icon(self):
        """图标 = 电量数字 + 等级底色。

        直接显示百分比数字；底色编码状态：灰=未连接、蓝=充电中、
        红=≤20%、黄=21..50%、绿=≥51%。白字配深底色，任务栏明暗都看得清。
        """
        Image, ImageDraw = self._Image, self._ImageDraw
        size = self.ICON_SIZE
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        color = self._level_color(self._connected, self._charging, self._battery)
        draw.rounded_rectangle([1, 1, size - 2, size - 2], radius=size // 4, fill=color)

        text = "--" if not self._connected else str(self._battery)
        inset = 9
        font = self._fit_font(text, size - inset * 2, size - inset * 2)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        draw.text(
            ((size - (right - left)) / 2 - left, (size - (bottom - top)) / 2 - top),
            text,
            font=font,
            fill=(255, 255, 255),
        )
        return image

    def _refresh_icon(self) -> None:
        self.icon.icon = self._render_icon()
        self.icon.title = f"rapoo-autoswitch — {self._status_line()}"

    # -- 后台轮询 ---------------------------------------------------------
    STATUS_POLL_MS = 200
    """单次状态读取的超时。循环调用，一有帧立刻返回，不必等满整个窗口。"""

    APPLY_ATTEMPTS = 3
    APPLY_RETRY_S = 0.5

    def _apply_preset(self, session) -> None:
        """把本机预设下发给鼠标。

        设备休眠或刚被唤醒时偶发"回读无应答 / 读到脏数据"，整组重试即可
        （写同样的值幂等）。实测：连续 6 次下发，头两次会失败，重试后全过。
        """
        snapshot = profiles.load(self.preset)
        last: Optional[Exception] = None
        for attempt in range(1, self.APPLY_ATTEMPTS + 1):
            try:
                written = profiles.apply(session, snapshot)
                self._note = f"已下发 {len(written)} 项"
                log.info("下发预设「%s」：%d 项寄存器", self.preset, len(written))
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("下发失败（第 %d/%d 次）：%s", attempt, self.APPLY_ATTEMPTS, exc)
                time.sleep(self.APPLY_RETRY_S)
        self._note = f"下发失败：{last}"
        log.error("下发预设「%s」失败（已重试 %d 次）：%s", self.preset, self.APPLY_ATTEMPTS, last)

    def _take_pending(self) -> bool:
        with self._lock:
            pending = self._pending_apply
            self._pending_apply = False
        return pending

    def _worker(self) -> None:
        session = None
        connected = False
        while not self._stop.is_set():
            try:
                found = dev.first_device(self.vendor_id)
            except Exception as exc:  # noqa: BLE001
                log.error("枚举设备失败：%s", exc)
                found = None

            if found is None:
                if session is not None:
                    session.transport.close()
                    session = None
                with self._lock:
                    changed = connected
                    connected = False
                    self._connected = False
                    self._battery = 0
                    self._charging = False
                    self._pending_apply = False
                if changed:
                    self._refresh_icon()
                self._stop.wait(self.interval)
                continue

            if session is None:
                try:
                    session = dev.open_session(found)
                    session.unlock()
                except Exception as exc:  # noqa: BLE001
                    log.error("打开设备失败：%s", exc)
                    session = None
                    self._stop.wait(self.interval)
                    continue
                with self._lock:
                    connected = True
                    self._connected = True
                if self.preset:
                    self._apply_preset(session)  # 接入即下发
                self._refresh_icon()

            # 本轮（interval 秒）内**持续**读状态，而不是只开一个小窗口。
            # 设备只在有活动/事件时才推帧，窗口太短很容易整帧错过——表现就是
            # 插上充电线后，电量/充电图标几十秒才变一下。
            deadline = time.monotonic() + self.interval
            while not self._stop.is_set() and time.monotonic() < deadline:
                try:
                    report = session.read_status(self.STATUS_POLL_MS)
                except Exception as exc:  # noqa: BLE001 - 拔出设备时句柄会报错
                    log.error("读取状态失败：%s", exc)
                    session.transport.close()
                    session = None
                    connected = False
                    break
                if report is not None:
                    with self._lock:
                        self._connected = True
                        self._battery = report.battery
                        self._charging = report.charging
                    self._refresh_icon()
                if self._take_pending():
                    self._apply_preset(session)

        if session is not None:
            try:
                session.transport.close()
            except Exception:  # noqa: BLE001
                pass

    # -- 入口 -------------------------------------------------------------
    def run(self) -> None:
        def setup(icon):
            icon.visible = True
            threading.Thread(target=self._worker, name="rapoo-poll", daemon=True).start()

        log.info("托盘启动，预设=%s", self.preset or "（未设置）")
        self.icon.run(setup=setup)
