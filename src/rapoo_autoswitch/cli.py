"""命令行入口。

常用流程：

    rapoo-autoswitch list                     # 看有没有连上
    rapoo-autoswitch snapshot 办公             # 把当前配置存成快照
    rapoo-autoswitch use 办公                  # 声明"这台电脑用「办公」"
    rapoo-autoswitch daemon                   # 常驻，接入即自动下发
    rapoo-autoswitch startup install          # 开机自启（Windows）
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from typing import IO, List, Optional

from . import __version__, autostart, daemon, doctor
from . import device as dev
from . import profiles
from . import protocol as p
from .config import config_dir, load_config, machine_name, set_preset


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _open_session(vendor_id: int):
    found = dev.first_device(vendor_id)
    if found is None:
        raise SystemExit(
            "未找到雷柏二代鼠标。请确认鼠标已连接，并已安装依赖：pip install hid"
        )
    return found, dev.open_session(found)


def _log_stream() -> Optional[IO[str]]:
    """托盘脱离控制台后用来接住日志的文件；开不出来就退回空设备。"""
    try:
        return open(config_dir() / "tray.log", "a", encoding="utf-8")
    except OSError:
        try:
            return open(os.devnull, "w", encoding="utf-8")
        except OSError:
            return None


def _detach_console() -> None:
    """从当前控制台脱离，并把输出改写到日志文件。

    托盘是常驻后台程序。若从终端启动，它挂在那个控制台上，**终端一关就被一起
    杀掉**（用户实际遇到的坑）。调用 ``FreeConsole`` 脱离之后，终端关闭不再
    影响它；双击启动时那个黑框也会因为"没有进程附着"而自动消失。

    脱离后原来的 stdout/stderr 句柄已失效，再写会报错，所以顺手改成日志文件——
    后台跑的东西出了问题总得有个地方可查。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.FreeConsole()
    except Exception:  # noqa: BLE001 - 脱不了就照常跑
        return
    stream = _log_stream()
    if stream is not None:
        sys.stdout = stream
        sys.stderr = stream


def _spawn_detached(argv: List[str]) -> None:
    """把常驻程序拉成独立进程，和当前控制台彻底断开。

    菜单里选「托盘常驻」时用：当前进程还要继续显示菜单，不能自己也脱掉控制台，
    所以另起一个进程去跑 tray。这样关掉菜单窗口也不会带走托盘。
    """
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, *argv]
    else:
        cmd = [sys.executable, "-m", "rapoo_autoswitch", *argv]
    # DETACHED_PROCESS | CREATE_NO_WINDOW：不附控制台，也不弹黑框
    flags = (0x00000008 | 0x08000000) if sys.platform == "win32" else 0
    try:
        subprocess.Popen(cmd, creationflags=flags, close_fds=True)
    except OSError as exc:
        print(f"  启动失败：{exc}")


def _acquire_singleton() -> bool:
    """标记"托盘已在本机运行"，防止同时跑出两个。

    两个托盘会一起抢状态接口（HID 输入报文谁先读谁拿到），结果是两边都读不到
    电量。用带名字的内核互斥体判断：进程退出时系统自动释放，不会残留。
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW(None, False, "rapoo-autoswitch-tray")
        # 183 = ERROR_ALREADY_EXISTS，说明已经有一个在跑了
        return ctypes.get_last_error() != 183
    except Exception:  # noqa: BLE001 - 判断不了就放行
        return True


def _hide_console() -> None:
    """把自己的控制台窗口藏起来。

    给开机自启的 ``--hidden`` 用：那时用户不想看见黑框。托盘另有
    ``_detach_console`` 直接脱控制台，连窗口都不需要藏。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:  # noqa: BLE001 - 拿不到窗口就算了，不影响功能
        pass


def _decode(reg: p.Register, raw: bytes) -> str:
    if reg.kind == "dpi_table":
        values = reg.decode(raw)
        return " / ".join(str(v) for v in values) if values else "（空）"
    if reg.kind == "blob":
        return raw.hex(" ")
    if reg.name == "linear_ripple":
        byte = raw[0]
        return f"直线修正{'关' if byte & 0x01 else '开'}，波纹{'关' if byte & 0x02 else '开'}"
    value = reg.decode(raw)
    if reg.kind == "polling":
        return f"{value} Hz"
    if reg.kind == "bool":
        return "开" if value else "关"
    if reg.kind == "minutes":
        return f"{value} 分钟"
    return str(value)


def _parse_value(reg: p.Register, text: str):
    lowered = text.strip().lower()
    if reg.kind in ("dpi_table", "blob"):
        return text  # encode() 会解析这类"整段"取值
    if reg.kind == "bool":
        return lowered in ("1", "on", "true", "yes", "开", "true")
    return int(text, 0)


# --------------------------------------------------------------------------
# 子命令实现
# --------------------------------------------------------------------------
def cmd_list(args) -> int:
    devices = dev.enumerate_devices(args.vendor_id)
    if not devices:
        print("没有检测到雷柏二代鼠标。")
        return 1
    for device in devices:
        print(f"* {device}")
        for role, path in sorted(device.paths.items()):
            print(f"    {role:<8} {path}")
    return 0


def _no_broadcast_hint() -> None:
    print("没有收到状态广播。")
    print("排查建议：")
    print("  1) 晃一下鼠标再试——状态报文通常是设备主动推的，鼠标休眠时会停推；")
    print("     默认休眠 10 分钟，动一下或按个键唤醒即可；")
    print("  2) 确认没有别的程序正占用状态接口：HID 输入报文是「谁先读谁拿到」，")
    print("     不是广播给所有进程。本工具的 tray、官方 A-Hub 都会把报文读走，")
    print("     请先退出它们（托盘图标右键→退出）；")
    print("  3) 用 `rapoo-autoswitch watch` 直接看原始字节：")
    print("     一帧都没有 = 报文被别处读走；有帧但显示「未识别」= 解析规则要调整。")


def _performance_lines(session) -> List[str]:
    """读回报率与性能模式（A-Hub 的"扫描率"档位），拼成 status 的附加行。

    这两项在状态广播里没有，得主动读寄存器——和电量走的是两条独立的路，所以
    即使 A-Hub 抢走状态接口、收不到广播，它们照样显示得出来。
    """
    lines: List[str] = []
    polling: Optional[int] = None
    try:
        reg = p.REGISTRY["polling_hz"]
        polling = reg.decode(session.read_register(reg))
        lines.append(f"  回报率    {polling} Hz")
    except dev.DeviceError as exc:
        lines.append(f"  回报率    读取失败（{exc}）")

    try:
        raw = session.read_register(p.REGISTRY["performance_mode"])
        name = p.performance_mode_name(polling, raw)
        # 档位名只认实测确认过的组合；没见过的就如实报字节，不猜
        shown = name if name else f"未知（{raw.hex(' ')}）"
        lines.append(f"  性能模式  {shown}")
    except dev.DeviceError as exc:
        lines.append(f"  性能模式  读取失败（{exc}）")
    return lines


def cmd_status(args) -> int:
    device, session = _open_session(args.vendor_id)
    with session:
        extra = _performance_lines(session)
        report = session.wait_status(timeout_s=args.timeout)
    if report is None:
        print(device.model)
        print("\n".join(extra))
        print()
        _no_broadcast_hint()
        return 1
    link = "有线" if report.wired else "2.4G"
    charge = "，充电中" if report.charging else ""
    print(f"{device.model}（{link}{charge}）")
    print(f"  电量      {report.battery}%")
    print(f"  DPI 档位  {report.dpi_level}")
    print(f"  DPI       {report.dpi_x} x {report.dpi_y}")
    print("\n".join(extra))
    print(f"  状态位    0x{report.status_byte:02x}")
    return 0


def cmd_watch(args) -> int:
    """直接打印状态接口收到的原始字节，用于排查"收不到状态广播"。

    能区分两种完全不同的情况：

    * **一条都没有** —— 报文被别的程序读走了（HID 输入报文谁先读谁拿到），
      或者该接口根本没有数据；
    * **有帧但显示「未识别」** —— 数据到我们手上了，是解析规则（设备标记、
      报文 ID）需要调整。
    """
    device, session = _open_session(args.vendor_id)
    print(f"监听 {device} 的状态接口 {args.seconds} 秒，请晃动鼠标…")
    deadline = time.monotonic() + args.seconds
    seen = 0
    with session:
        while time.monotonic() < deadline and seen < args.count:
            raw = session.transport.read_status(args.timeout_ms)
            if not raw:
                continue
            seen += 1
            report = p.parse_status(raw)
            print(f"[{seen}] {'状态帧' if report else '未识别'} len={len(raw)}  {bytes(raw).hex(' ')}")
            if report is not None:
                link = "有线" if report.wired else "2.4G"
                print(
                    f"      电量 {report.battery}%  {link}  "
                    f"档位 {report.dpi_level}  DPI {report.dpi_x}x{report.dpi_y}"
                )
    print(f"共收到 {seen} 帧")
    if seen == 0:
        print("一帧都没有 -> 报文很可能是被其它程序读走了（见 `status` 的排查建议第 2 条）。")
    return 0


def cmd_battery(args) -> int:
    device, session = _open_session(args.vendor_id)
    with session:
        report = session.wait_status(timeout_s=args.timeout)
    if report is None:
        print("没有收到状态广播。")
        return 1
    suffix = " [charging]" if report.charging else ""
    print(f"{report.battery}%{suffix}")
    return 0


def cmd_get(args) -> int:
    device, session = _open_session(args.vendor_id)
    names = args.names or [reg.name for reg in p.SYSTEM_REGISTERS]
    with session:
        for name in names:
            reg = p.REGISTRY.get(name)
            if reg is None:
                print(f"{name}: 未知寄存器")
                continue
            try:
                raw = session.read_register(reg)
                print(f"{name:<16} {_decode(reg, raw):<12} (0x{reg.addr:02x}/{reg.bank:02x} = {raw.hex()})")
            except dev.DeviceError as exc:
                print(f"{name:<16} 读取失败: {exc}")
    return 0


def cmd_set(args) -> int:
    assignments = []
    for item in args.assignments:
        if "=" not in item:
            raise SystemExit(f"参数格式应为 名称=值，收到：{item}")
        name, value = item.split("=", 1)
        reg = p.REGISTRY.get(name.strip())
        if reg is None:
            raise SystemExit(f"未知寄存器：{name.strip()}")
        assignments.append((reg, _parse_value(reg, value)))

    _, session = _open_session(args.vendor_id)
    with session:
        for reg, value in assignments:
            data = reg.encode(value)
            session.write_register(reg, data)
            print(f"{reg.name:<16} -> {_decode(reg, data)}")
    return 0


def cmd_dpi(args) -> int:
    """查看 / 设置 DPI 档位。

    设置时严格按协议顺序下发：表 X → 表 Y → 档位数 → 当前档位，并把当前档位
    夹到新档位数范围内，避免设备引用了不存在的档位。
    """
    _, session = _open_session(args.vendor_id)
    with session:
        if args.stages:
            values = [int(v) for v in args.stages.replace(",", " ").split()]
            count = len(values)
            if not 1 <= count <= p.DPI_MAX_SLOTS:
                raise SystemExit(f"档位数需在 1..{p.DPI_MAX_SLOTS} 之间，收到 {count}")
            table = b"".join(v.to_bytes(2, "little") for v in values)
            session.write_raw(p.BANK_SYSTEM, p.ADDR_DPI_TABLE_X, table)
            session.write_raw(p.BANK_SYSTEM, p.ADDR_DPI_TABLE_Y, table)
            session.write_raw(p.BANK_SYSTEM, p.ADDR_DPI_SLOT_COUNT, bytes([count - 1]))
            index = min(max(args.index or 1, 1), count)
            session.write_raw(p.BANK_SYSTEM, p.ADDR_DPI_ACTIVE_INDEX, bytes([index - 1]))
            print(f"已写入 {count} 档 DPI：{' / '.join(str(v) for v in values)}，当前第 {index} 档")
            return 0

        if args.index:
            count = session.read_register(p.REGISTRY["dpi_slot_count"])[0] + 1
            index = min(max(args.index, 1), count)
            session.write_raw(p.BANK_SYSTEM, p.ADDR_DPI_ACTIVE_INDEX, bytes([index - 1]))
            print(f"当前档位已切到第 {index} 档（共 {count} 档）")
            return 0

        count = session.read_register(p.REGISTRY["dpi_slot_count"])[0] + 1
        index = session.read_register(p.REGISTRY["dpi_active_index"])[0] + 1
        axis_x = p.REGISTRY["dpi_table_x"].decode(
            session.read_register(p.REGISTRY["dpi_table_x"])
        )
        axis_y = p.REGISTRY["dpi_table_y"].decode(
            session.read_register(p.REGISTRY["dpi_table_y"])
        )

    print(f"档位数 {count}，当前第 {index} 档")
    for slot in range(min(count, len(axis_x))):
        x = axis_x[slot]
        y = axis_y[slot] if slot < len(axis_y) else x
        suffix = f"（Y 轴 {y}）" if y != x else ""
        mark = "  <- 当前" if slot + 1 == index else ""
        print(f"  {slot + 1}. {x} DPI{suffix}{mark}")
    return 0


def cmd_dump(args) -> int:
    """把一段地址整体读出来逐行打印，用于对比不同配置的差异。

    典型用法——定位 A-Hub 两套配置到底差在哪::

        rapoo-autoswitch dump 0x08 > 出厂.txt
        （在 A-Hub 里切到另一套配置）
        rapoo-autoswitch dump 0x08 > 办公.txt
        fc 出厂.txt 办公.txt
    """
    _, session = _open_session(args.vendor_id)
    rows = []
    with session:
        addr = args.start
        while addr <= args.end:
            length = min(args.chunk, args.end - addr + 1, p.MAX_PAYLOAD)
            if length <= 0:
                break
            try:
                rows.append((addr, session.read_raw(args.bank, addr, length)))
            except dev.DeviceError as exc:
                rows.append((addr, f"<读取失败: {exc}>"))
            addr += length

    print(f"# bank 0x{args.bank:02x}  addr 0x{args.start:02x}..0x{args.end:02x}")
    for addr, raw in rows:
        if isinstance(raw, str):
            print(f"{addr:02x}: {raw}")
        else:
            print(f"{addr:02x}: {raw.hex(' ')}")
    return 0


def cmd_probe(args) -> int:
    """原始读取，用于逆向新机型/新寄存器。"""
    _, session = _open_session(args.vendor_id)
    with session:
        raw = session.read_raw(args.bank, args.addr, args.length)
    print(f"bank=0x{args.bank:02x} addr=0x{args.addr:02x} len={args.length}")
    print("hex:", raw.hex(" "))
    print("u8 :", " ".join(str(b) for b in raw))
    return 0


def cmd_snapshot(args) -> int:
    device, session = _open_session(args.vendor_id)
    with session:
        snapshot = profiles.capture(session, args.name, device.product_id)
    if not snapshot.registers:
        print("没有采到任何寄存器，已放弃保存。")
        return 1
    path = profiles.save(snapshot)
    print(f"已保存快照 {snapshot.describe()}")
    for key in sorted(snapshot.registers):
        print(f"  {profiles.display_name(key):<16} {snapshot.registers[key]}")
    print(f"路径：{path}")
    return 0


def cmd_snapshots(args) -> int:
    names = profiles.list_names()
    if not names:
        print("还没有任何快照。用 `snapshot <名字>` 创建。")
        return 0
    preset = load_config().get("preset")
    for name in names:
        try:
            snapshot = profiles.load(name)
            mark = "  <- 本机预设" if name == preset else ""
            print(f"* {snapshot.describe()}{mark}")
        except Exception as exc:  # noqa: BLE001
            print(f"* {name}（读取失败：{exc}）")
    return 0


def cmd_apply(args) -> int:
    snapshot = profiles.load(args.name)
    device, session = _open_session(args.vendor_id)
    with session:
        if args.dry_run:
            changes = profiles.diff(session, snapshot)
            if not changes:
                print("当前配置与快照一致，无需下发。")
                return 0
            print(f"以下 {len(changes)} 项将发生变化：")
            for key, (current, target) in sorted(changes.items()):
                print(f"  {profiles.display_name(key):<16} {current} -> {target}")
            return 0
        written = profiles.apply(session, snapshot)
    print(f"已下发快照「{snapshot.name}」到 {device.model}：{len(written)} 项寄存器")
    return 0


def cmd_use(args) -> int:
    # 先确认快照存在，避免写入一个不存在的预设
    profiles.load(args.name)
    config = set_preset(args.name)
    print(f"本机（{machine_name()}）预设已设为「{config['preset']}」")
    print("运行 `rapoo-autoswitch daemon` 即可在接入时自动下发。")
    return 0


def cmd_current(args) -> int:
    config = load_config()
    preset = config.get("preset")
    print(f"本机      {machine_name()}")
    print(f"预设      {preset or '（未设置）'}")
    print(f"探测间隔  {config.get('poll_interval_s')}s")
    print(f"VID       0x{int(config.get('vendor_id', p.VENDOR_ID)):04x}")
    return 0


def cmd_tray(args) -> int:
    from .tray import POLL_S, TrayApp

    if not _acquire_singleton():
        print("托盘已经在运行了，看右下角托盘区的小图标。这个窗口可以关掉。")
        return 0
    if getattr(args, "hidden", False):
        _hide_console()
    # 托盘是长住后台的：先脱离控制台，这样从终端启动后关掉终端也不影响它
    _detach_console()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = TrayApp(preset=args.preset, interval=args.interval or POLL_S)
    app.run()
    return 0


def cmd_daemon(args) -> int:
    if getattr(args, "hidden", False):
        _hide_console()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    def on_event(name, payload):
        if name == "applied":
            pass  # 具体日志已在 daemon 内打印

    try:
        daemon.run(
            preset=args.preset,
            interval=args.interval,
            vendor_id=args.vendor_id,
            once=args.once,
            on_event=on_event,
        )
    except KeyboardInterrupt:
        print("守护已停止。")
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


def cmd_startup(args) -> int:
    if args.action == "install":
        cmd = autostart.install(args.mode)
        print(f"已写入开机自启（{args.mode}）：{cmd}")
    elif args.action == "uninstall":
        autostart.uninstall()
        print("已移除开机自启。")
    else:
        current = autostart.current()
        print(f"开机自启：{current or '（未设置）'}")
    return 0


def cmd_doctor(args) -> int:
    """环境自检；装不上或用不了时先跑这个。"""
    return doctor.run()


def _ask(prompt: str, default: str = "") -> str:
    """读一行输入；非交互（管道/EOF）时返回默认值，方便脚本调用。"""
    try:
        answer = input(prompt).strip()
    except EOFError:
        return default
    return answer or default


def cmd_setup(args) -> int:
    """交互式向导：采集快照 → 设为本机预设 → 选择常驻方式。"""
    print("rapoo-autoswitch 上手向导（直接回车 = 用括号里的默认值）")
    print()

    found = dev.first_device(args.vendor_id)
    if found is None:
        print("没找到雷柏二代鼠标。插好接收器/鼠标后再跑一次。")
        return 1
    print(f"检测到：{found}")
    print()

    name = _ask("快照名（会保存当前鼠标配置）[本机]：", "本机")
    _, session = _open_session(args.vendor_id)
    with session:
        snapshot = profiles.capture(session, name, found.product_id)
    if not snapshot.registers:
        print("没有采到任何寄存器，已放弃。")
        return 1
    profiles.save(snapshot)
    print(f"已保存快照「{name}」（{snapshot.size} 项）")

    if _ask("把本机预设设为它？[Y/n]：", "y").lower() in ("y", "yes", "是"):
        set_preset(name)
        print(f"本机预设 → 「{name}」")

    print()
    print("常驻方式：")
    print("  1) daemon  无界面，插上就自动下发（推荐）")
    print("  2) tray    托盘图标，能看电量、右键切配置")
    print("  3) 先不装，我自己跑")
    choice = _ask("选择 [1]：", "1")
    mode = {"1": "daemon", "2": "tray", "daemon": "daemon", "tray": "tray"}.get(choice)
    if mode:
        try:
            cmd = autostart.install(mode)
            print(f"已设置开机自启（{mode}）：{cmd}")
        except RuntimeError as exc:
            print(f"跳过开机自启：{exc}")
    else:
        print("跳过开机自启。")

    print()
    print("完成。注意两点：")
    print("  · 别让官方 A-Hub 常驻——它会抢状态接口，还可能改掉你的配置；")
    print("  · 换电脑后，在那台电脑上各跑一次本向导即可。")
    return 0


# --------------------------------------------------------------------------
# 参数解析
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rapoo-autoswitch",
        description="按电脑自动切换雷柏二代鼠标（VT3S V2 等）的配置",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--vendor-id", type=lambda v: int(v, 0), default=p.VENDOR_ID,
        help="雷柏 VID，默认 0x24ae",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="环境自检：装不上或用不了，先跑它").set_defaults(func=cmd_doctor)

    sub.add_parser("setup", help="上手向导：采集配置、设预设、装开机自启").set_defaults(
        func=cmd_setup
    )

    sub.add_parser("list", help="列出已连接的雷柏二代鼠标").set_defaults(func=cmd_list)

    status = sub.add_parser("status", help="查看电量 / DPI / 连接方式")
    status.add_argument(
        "--timeout", type=float, default=6.0,
        help="等待状态广播的秒数（设备约每 3 秒推一帧，默认 6 秒足够跨过一整个周期）",
    )
    status.set_defaults(func=cmd_status)

    watch = sub.add_parser("watch", help="打印状态接口的原始字节，排查收不到广播")
    watch.add_argument("--seconds", type=float, default=10.0, help="监听时长，默认 10 秒")
    watch.add_argument("--timeout-ms", type=int, default=100, help="单次读取超时，默认 100ms")
    watch.add_argument("--count", type=int, default=20, help="收到多少帧后停止，默认 20")
    watch.set_defaults(func=cmd_watch)

    battery = sub.add_parser("battery", help="只输出电量")
    battery.add_argument("--timeout", type=float, default=6.0)
    battery.set_defaults(func=cmd_battery)

    get = sub.add_parser("get", help="读取寄存器并解码")
    get.add_argument("names", nargs="*", help="寄存器名，缺省为全部已知寄存器")
    get.set_defaults(func=cmd_get)

    set_ = sub.add_parser("set", help="写入寄存器，如 set polling_hz=1000")
    set_.add_argument("assignments", nargs="+")
    set_.set_defaults(func=cmd_set)

    dpi = sub.add_parser("dpi", help="查看或设置 DPI 档位")
    dpi.add_argument("stages", nargs="?", help="各档 DPI，如 800,1600,3200")
    dpi.add_argument("--index", type=int, help="切换当前档位（1 起）")
    dpi.set_defaults(func=cmd_dpi)

    dump = sub.add_parser("dump", help="整段读出寄存器，用于对比不同配置的差异")
    dump.add_argument("bank", nargs="?", type=lambda v: int(v, 0), default=p.BANK_SYSTEM)
    dump.add_argument("start", nargs="?", type=lambda v: int(v, 0), default=0x00)
    dump.add_argument("end", nargs="?", type=lambda v: int(v, 0), default=0xFF)
    dump.add_argument("--chunk", type=lambda v: int(v, 0), default=16, help="单帧读取长度，默认 16")
    dump.set_defaults(func=cmd_dump)

    probe = sub.add_parser("probe", help="原始读取，用于逆向新机型")
    probe.add_argument("bank", type=lambda v: int(v, 0))
    probe.add_argument("addr", type=lambda v: int(v, 0))
    probe.add_argument("length", type=lambda v: int(v, 0))
    probe.set_defaults(func=cmd_probe)

    snapshot = sub.add_parser("snapshot", help="把当前配置存成快照")
    snapshot.add_argument("name")
    snapshot.set_defaults(func=cmd_snapshot)

    sub.add_parser("snapshots", help="列出本机所有快照").set_defaults(func=cmd_snapshots)

    apply_ = sub.add_parser("apply", help="把快照下发给鼠标")
    apply_.add_argument("name")
    apply_.add_argument("--dry-run", action="store_true", help="只显示差异，不写入")
    apply_.set_defaults(func=cmd_apply)

    use = sub.add_parser("use", help="设置本机预设")
    use.add_argument("name")
    use.set_defaults(func=cmd_use)

    sub.add_parser("current", help="显示本机配置").set_defaults(func=cmd_current)

    daemon_parser = sub.add_parser("daemon", help="常驻，鼠标接入时自动下发本机预设")
    daemon_parser.add_argument("--preset", help="临时指定快照，覆盖本机预设")
    daemon_parser.add_argument("--interval", type=float, help="探测间隔（秒）")
    daemon_parser.add_argument("--once", action="store_true", help="只跑一轮，便于调试")
    daemon_parser.add_argument("--hidden", action="store_true", help=argparse.SUPPRESS)
    daemon_parser.set_defaults(func=cmd_daemon)

    tray_parser = sub.add_parser("tray", help="托盘常驻版：显示电量、切换配置")
    tray_parser.add_argument("--preset", help="启动时使用的快照，覆盖本机预设")
    tray_parser.add_argument("--interval", type=float, help="电量刷新间隔（秒）")
    tray_parser.add_argument("--hidden", action="store_true", help=argparse.SUPPRESS)
    tray_parser.set_defaults(func=cmd_tray)

    startup = sub.add_parser("startup", help="开机自启（Windows）")
    startup.add_argument("action", choices=["install", "uninstall", "status"])
    startup.add_argument(
        "--mode", choices=autostart.MODES, default="daemon",
        help="install 时自启哪一种常驻：daemon=无界面守护（默认），tray=托盘版（带电量图标）",
    )
    startup.set_defaults(func=cmd_startup)

    return parser


_MENU = (
    ("1", "配置向导", "采集当前配置 → 设为本机预设 → 装开机自启", ["setup"]),
    ("2", "环境自检", "装不上 / 用不了，先跑它", ["doctor"]),
    ("3", "查看状态", "电量 / DPI / 连接方式", ["status"]),
    ("4", "托盘常驻", "后台运行，右下角图标看电量、右键切配置", ["tray"]),
)


def _pause(prompt: str = "按回车继续…") -> None:
    try:
        input(prompt)
    except EOFError:
        pass


def _no_args() -> int:
    """没带参数时（双击 exe 的典型情形）给一个菜单。

    双击的用户不会去敲参数，所以这里不能只丢一堆 usage 就退出。
    """
    if not (sys.stdin is not None and sys.stdin.isatty()):
        # 非交互（管道 / 重定向）：只打印用法，别把调用方卡住
        print("rapoo-autoswitch —— 按电脑自动切换雷柏二代鼠标配置")
        print()
        print("常用：rapoo-autoswitch  setup / doctor / status / tray / daemon")
        print()
        build_parser().print_help()
        return 0

    while True:
        print()
        print("  rapoo-autoswitch —— 按电脑自动切换雷柏二代鼠标配置")
        print()
        for key, title, desc, _ in _MENU:
            print(f"  {key}) {title:<8} {desc}")
        print("  0) 退出")
        print()
        try:
            choice = input("  请输入序号 [1]：").strip() or "1"
        except EOFError:
            # 输入流已经结束（句柄被关掉之类）：直接退出。这里**不能**退回默认值，
            # 否则会在没人操作的情况下把「1) 配置向导」反复跑下去。
            print()
            return 0
        if choice in ("0", "q", "quit", "exit"):
            return 0
        action = next((argv for key, _, _, argv in _MENU if key == choice), None)
        if action is None:
            print("  没有这个选项，重新选一下。")
            continue
        if action == ["tray"]:
            # 托盘要长住后台：另起一个脱离控制台的进程去跑，菜单这边继续留着，
            # 否则它会把本进程的控制台一起脱掉，菜单就看不见了。
            print("  正在后台启动托盘…")
            _spawn_detached(action)
            _pause()
            continue
        try:
            main(action)
        except KeyboardInterrupt:
            print()
        _pause()


def _make_stdout_safe() -> None:
    """输出编码兜底。

    中文 Windows 上，输出一旦被重定向（管道、写文件），Python 就用 GBK 编码，
    遇到不能编码的字符会直接抛 ``UnicodeEncodeError`` 把命令打断。这里降级成
    替换字符，保证命令不会因为一行输出而崩掉。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001 - 老版本/被包装过的流没有 reconfigure
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _make_stdout_safe()
    raw = sys.argv[1:] if argv is None else argv
    if not raw:
        return _no_args()

    parser = build_parser()
    args = parser.parse_args(raw)
    try:
        return args.func(args)
    except (dev.DeviceError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
