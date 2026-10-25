"""托盘常驻版：任务栏显示电量，右键切换配置。

面向 Windows，依赖 ``pystray`` + ``Pillow``（可选依赖）：

    pip install "rapoo-autoswitch[tray]"
    rapoo-autoswitch tray

与 ``daemon`` 的区别：托盘版是常驻 GUI，能看电量、能手动切配置；``daemon`` 是
无界面的守护进程，适合配开机自启。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import List, Optional

from . import device as dev
from . import profiles
from . import protocol as p
from .config import load_config, snapshots_dir, set_preset

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
        self._session = None

        self.icon = pystray.Icon(
            "rapoo-autoswitch",
            icon=self._render_icon(),
            title="rapoo-autoswitch",
            menu=self._menu_items(),
        )

    # -- 菜单 -------------------------------------------------------------
    def _menu_items(self):
        """即时生成当前托盘菜单项（每次刷新都重新读快照目录）。

        不能在构造时把 items 存成一次性生成器：Windows 后端的 HMENU
        只在 ``_update_menu`` 时完整重建一次；callable-items 的
        ``Menu`` 虽然每次访问 ``items`` 都重新迭代，但没人会再去访问它。
        所以保存/删除后必须直接重建整个 menu（``icon.menu = ...``）。
        """
        pystray = self._pystray
        items = [
            pystray.MenuItem(lambda _: self._status_line(), None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]
        names = profiles.list_names()
        if names:
            for name in names:
                # 二级目录：每个预设一个子菜单，展开后可"切换"或"删除"
                items.append(pystray.MenuItem(
                    f"预设：{name}",
                    pystray.Menu(
                        pystray.MenuItem(
                            f"切换到「{name}」",
                            self._make_switcher(name),
                            checked=lambda _item, n=name: n == self.preset,
                            radio=True,
                        ),
                        pystray.MenuItem(
                            f"✕  删除「{name}」",
                            self._make_deleter(name),
                            enabled=lambda _item, n=name: n != self.preset,
                        ),
                    ),
                ))
        else:
            items.append(pystray.MenuItem("预设：（还没有快照）", None, enabled=False))

        items.extend([
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "＋  保存当前预设并复制",
                self._save_and_copy,
                enabled=lambda _: self._connected,
            ),
            pystray.MenuItem(
                "↻  立即重新下发",
                self._reapply,
                enabled=lambda _: bool(self.preset),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("查看预设数据", self._show_all_presets),
            pystray.MenuItem("刷新电量", lambda: self._refresh_icon()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        ])
        return pystray.Menu(*items)

    # -- 查看预设数据 -------------------------------------------------------
    def _show_all_presets(self, _icon=None, _item=None) -> None:
        """打开一个 Tkinter 窗口，列出所有预设及其完整寄存器数据。"""
        threading.Thread(target=self._show_preset_data, args=(), daemon=True).start()

    def _show_preset_data(self, name: str = "") -> None:
        """弹出 Tkinter 窗口展示预设的详细寄存器数据。

        如果 name 为空则展示所有预设。
        """
        import tkinter as tk

        names = [name] if name else profiles.list_names()
        if not names:
            self._note = "没有预设可查看"
            return

        reg_table = p.REGISTRY_BY_KEY
        lines = []
        for nm in names:
            snap = profiles.load(nm)
            if snap is None:
                lines.append(f"预设「{nm}」: 文件不存在\n")
                continue
            lines.append(f"=== 预设: {snap.name}  PID: 0x{snap.product_id:04x} ===")
            lines.append(f"采集时间: {snap.created}")
            lines.append(f"寄存器项数: {snap.size}")
            lines.append("")
            lines.append(f"  {'地址':<8}  {'名称':<22}  {'值':<24}")
            lines.append("  " + "-" * 60)
            sorted_regs = sorted(
                snap.registers.items(),
                key=lambda x: (reg_table[x[0]].order if x[0] in reg_table else 1000),
            )
            for key, value in sorted_regs:
                reg = reg_table.get(key)
                hex_v = value if isinstance(value, str) else f"0x{value:02x}"
                if reg:
                    display_name = reg.label or reg.name
                    try:
                        raw = bytes.fromhex(hex_v.replace(" ", ""))
                        decoded = reg.decode(raw)
                        extra = ""
                        if reg.kind == "polling":
                            extra = f" ({decoded} Hz)"
                        elif reg.kind == "keyscan":
                            hz = p.KEYSCAN_CODE_TO_HZ.get(decoded, decoded)
                            extra = f" ({hz} Hz)"
                        elif reg.kind == "bool":
                            extra = " [开启]" if decoded else " [关闭]"
                        elif reg.kind == "slot_count":
                            extra = f" ({decoded} 档)"
                        elif reg.kind == "dpi_index":
                            extra = f" (第{decoded}档)"
                        elif reg.kind == "dpi_table":
                            extra = f" [{', '.join(str(v) for v in decoded[:3])}, ...]" if len(decoded) > 3 else f" [{', '.join(str(v) for v in decoded)}]"
                        elif reg.kind == "minutes":
                            extra = " 分钟"
                        elif reg.kind == "angle":
                            extra = f"°"
                        elif reg.kind == "blob":
                            extra = " (仅备份，不自动下发)" if not reg.write_enabled else ""
                        lines.append(f"  0{reg.bank:02x}:{reg.addr:02x}  {display_name:<20}  {hex_v}{extra}")
                    except Exception:
                        lines.append(f"  0{reg.bank:02x}:{reg.addr:02x}  {display_name:<20}  {hex_v}")
                else:
                    lines.append(f"  {key:<8}  {'(未知寄存器)':<20}  {hex_v}")
            lines.append("")

        text = "\n".join(lines)

        def _build_window():
            root = tk.Tk()
            root.title("预设数据")
            root.geometry("720x560")
            root.configure(bg="#fafafa")
            root.minsize(560, 400)

            # 顶部信息栏
            top = tk.Frame(root, bg="#fafafa")
            top.pack(fill="x", padx=10, pady=(8, 4))
            tk.Label(top, text="预设寄存器数据", font=("Microsoft YaHei UI", 12, "bold"),
                     bg="#fafafa", fg="#1a1a1a").pack(side="left")
            tk.Label(top, text=f"共 {len(names)} 个预设", font=("Microsoft YaHei UI", 9),
                     bg="#fafafa", fg="#888").pack(side="right")

            # 正文
            text_frame = tk.Frame(root, bg="#fafafa")
            text_frame.pack(fill="both", expand=True, padx=10, pady=4)
            txt = tk.Text(text_frame, wrap="word", font=("Consolas", 9),
                          bg="white", relief="solid", bd=1,
                          padx=10, pady=8, insertbackground="#333")
            txt.insert("1.0", text)
            txt.configure(state="disabled")
            scrollbar = tk.Scrollbar(text_frame, command=txt.yview)
            txt.configure(yscrollcommand=scrollbar.set)
            txt.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            # 底部按钮
            btn_frame = tk.Frame(root, bg="#fafafa")
            btn_frame.pack(fill="x", padx=10, pady=(4, 8))
            tk.Button(btn_frame, text="关闭", command=root.destroy,
                      font=("Microsoft YaHei UI", 9),
                      bg="white", fg="#333", relief="flat", bd=0,
                      padx=14, pady=4, activebackground="#e8e8e8",
                      activeforeground="#111").pack(side="right")
            tk.Button(btn_frame, text="复制全部内容",
                      command=lambda: self._copy_text_to_clipboard(text),
                      font=("Microsoft YaHei UI", 9),
                      bg="#1a1a1a", fg="white", relief="flat", bd=0,
                      padx=14, pady=4, activebackground="#333",
                      activeforeground="white").pack(side="left")

            root.mainloop()

        threading.Thread(target=_build_window, daemon=True).start()

    def _copy_text_to_clipboard(self, text: str) -> None:
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            root.destroy()
        except Exception:
            pass

    def _rebuild_menu(self, delay: float = 0.0) -> None:
        """保存/删除预设后刷新托盘菜单。

        在后台线程里给 ``icon.menu`` 赋一个新 menu（items 用列表而非
        生成器），触发 pystray 的 ``update_menu()`` 重建 Windows HMENU。
        不依赖"下次弹出菜单时重新生成"——Windows 后端并不会那样做。
        """
        def _rebuild() -> None:
            if delay:
                time.sleep(delay)
            try:
                self.icon.menu = self._menu_items()
                log.info("托盘菜单已刷新")
            except Exception as exc:  # noqa: BLE001
                log.warning("刷新托盘菜单失败：%s", exc)

        threading.Thread(target=_rebuild, name="rapoo-menu-rebuild", daemon=True).start()

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

    def _make_deleter(self, name: str):
        def delete(_icon=None, _item=None):
            import tkinter as tk
            from tkinter import messagebox

            def _confirm():
                root = tk.Tk()
                root.withdraw()
                ok = messagebox.askyesno(
                    "删除预设",
                    f"确定删除预设「{name}」？\n此操作不可恢复。",
                    parent=root,
                )
                root.destroy()
                if not ok:
                    return
                try:
                    profiles.delete(name)
                    if self.preset == name:
                        self.preset = None
                    self._note = f"已删除预设「{name}」"
                    log.info("删除预设「%s」", name)
                    self._rebuild_menu(delay=0.1)
                except Exception as exc:  # noqa: BLE001
                    self._note = f"删除失败：{exc}"
                    log.error("删除预设「%s」失败：%s", name, exc)
                    self._rebuild_menu(delay=0.1)
                finally:
                    self._refresh_icon()

            threading.Thread(target=_confirm, daemon=True).start()

        return delete

    def _reapply(self, _icon=None, _item=None) -> None:
        self._request_apply()
        self._refresh_icon()

    def _save_and_copy(self, _icon=None, _item=None) -> None:
        """从设备采集当前设置，存为预设，自动切换并复制到剪贴板。"""
        with self._lock:
            session = self._session
        if session is None:
            self._note = "未检测到鼠标"
            self._refresh_icon()
            return
        try:
            # 生成友好的预设名：优先沿用当前预设名，否则用日期时间
            if self.preset and self.preset not in profiles.list_names():
                preset_name = self.preset
            else:
                preset_name = f"custom-{time.strftime('%m%d-%H%M')}"

            found = dev.first_device(self.vendor_id)
            pid = found.product_id if found else 0
            with self._lock:
                snap = profiles.capture(session, preset_name, pid)
                path = profiles.save(snap)
                # 自动切换到刚保存的预设
                self.preset = preset_name
                set_preset(preset_name)
            payload = json.dumps(
                {
                    "name": snap.name,
                    "product_id": f"0x{snap.product_id:04x}",
                    "created": snap.created,
                    "registers": snap.registers,
                    "saved_to": str(path),
                },
                ensure_ascii=False,
                indent=2,
            )
            self._set_clipboard(payload)
            self._note = f"已保存并切换预设「{snap.name}」，已复制到剪贴板"
            log.info(
                "保存并复制预设「%s」：%d 项寄存器 → %s",
                snap.name, snap.size, path,
            )
            self._rebuild_menu(delay=0.2)
        except Exception as exc:  # noqa: BLE001
            self._note = f"保存失败：{exc}"
            log.error("保存预设失败：%s", exc)
            self._rebuild_menu(delay=0.2)
        self._refresh_icon()

    @staticmethod
    def _set_clipboard(text: str) -> None:
        """把文本写入 Windows 剪贴板。

        优先用 ctypes 直接调 user32 API：win32clipboard 在 PyInstaller
        冻结环境下曾出现 ``SetClipboardData() takes exactly 2 arguments``
        的参数不兼容问题，ctypes 方式跨打包形态都稳定。
        失败时回退到 PowerShell（stdin 传入，避免 shell 转义问题）。
        """
        import ctypes

        CF_UNICODETEXT = 13
        user32 = ctypes.windll.user32
        try:
            if not user32.OpenClipboard(0):
                raise RuntimeError(f"OpenClipboard 失败（err={ctypes.get_last_error()})")
            opened = True
            user32.EmptyClipboard()
            buf = ctypes.create_unicode_buffer(text)
            handle = user32.GlobalAlloc(0x0042, len(text) * 2 + 2)
            if not handle:
                raise RuntimeError("GlobalAlloc 失败")
            ptr = user32.GlobalLock(handle)
            ctypes.memmove(ptr, ctypes.addressof(buf), len(text) * 2 + 2)
            user32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise RuntimeError("SetClipboardData 失败")
        except Exception as exc:  # noqa: BLE001
            if opened:
                user32.CloseClipboard()
            # 回退：PowerShell
            import subprocess
            ps_cmd = "Set-Clipboard -Value ([Console]::In.ReadToEnd())"
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=5,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"剪贴板写入失败：{proc.stderr.decode(errors='replace')}")
            return
        user32.CloseClipboard()

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
                    self._session = None
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
                    self._session = session
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
                    with self._lock:
                        self._session = None
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
