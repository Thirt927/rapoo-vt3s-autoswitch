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
import sys
from typing import List, Optional

from . import __version__, autostart, daemon
from . import device as dev
from . import profiles
from . import protocol as p
from .config import load_config, machine_name, set_preset


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


def _decode(reg: p.Register, raw: bytes) -> str:
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


def cmd_status(args) -> int:
    device, session = _open_session(args.vendor_id)
    with session:
        report = session.wait_status(timeout_s=args.timeout)
    if report is None:
        print("没有收到状态广播。动一下鼠标再试。")
        return 1
    link = "有线" if report.wired else "2.4G"
    charge = "，充电中" if report.charging else ""
    print(f"{device.model}（{link}{charge}）")
    print(f"  电量      {report.battery}%")
    print(f"  DPI 档位  {report.dpi_level}")
    print(f"  DPI       {report.dpi_x} x {report.dpi_y}")
    print(f"  状态位    0x{report.status_byte:02x}")
    return 0


def cmd_battery(args) -> int:
    device, session = _open_session(args.vendor_id)
    with session:
        report = session.wait_status(timeout_s=args.timeout)
    if report is None:
        print("没有收到状态广播。")
        return 1
    suffix = " ⚡" if report.charging else ""
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


def cmd_daemon(args) -> int:
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
        cmd = autostart.install()
        print(f"已写入开机自启：{cmd}")
    elif args.action == "uninstall":
        autostart.uninstall()
        print("已移除开机自启。")
    else:
        current = autostart.current()
        print(f"开机自启：{current or '（未设置）'}")
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

    sub.add_parser("list", help="列出已连接的雷柏二代鼠标").set_defaults(func=cmd_list)

    status = sub.add_parser("status", help="查看电量 / DPI / 连接方式")
    status.add_argument("--timeout", type=float, default=2.0, help="等待状态广播的秒数")
    status.set_defaults(func=cmd_status)

    battery = sub.add_parser("battery", help="只输出电量")
    battery.add_argument("--timeout", type=float, default=2.0)
    battery.set_defaults(func=cmd_battery)

    get = sub.add_parser("get", help="读取寄存器并解码")
    get.add_argument("names", nargs="*", help="寄存器名，缺省为全部已知寄存器")
    get.set_defaults(func=cmd_get)

    set_ = sub.add_parser("set", help="写入寄存器，如 set polling_hz=1000")
    set_.add_argument("assignments", nargs="+")
    set_.set_defaults(func=cmd_set)

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
    daemon_parser.set_defaults(func=cmd_daemon)

    startup = sub.add_parser("startup", help="开机自启（Windows）")
    startup.add_argument("action", choices=["install", "uninstall", "status"])
    startup.set_defaults(func=cmd_startup)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv or sys.argv[1:])
    try:
        return args.func(args)
    except (dev.DeviceError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
