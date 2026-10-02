"""设备发现与命令会话。

雷柏二代鼠标在同一个 VID/PID 下会暴露多个 HID 接口，靠 ``usage_page=0xFF00``
配合 ``usage`` 区分角色：

===========  =======  ==========================================
角色          usage    作用
===========  =======  ==========================================
control      0x000E   下发命令（report id 0x06）
feature      0x000F   读回命令应答（report id 0x08）
status       0x0002   接收状态广播（report id 0x07）
===========  =======  ==========================================

若枚举信息里拿不到 usage（部分平台/驱动如此），退化为按接口顺序分配。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import protocol as p
from .transport import HidapiTransport, Transport, hidapi_available

VENDOR_USAGE_PAGE = 0xFF00
USAGE_CONTROL = 0x000E
USAGE_FEATURE = 0x000F
USAGE_STATUS = 0x0002

MODEL_NAMES: Dict[int, str] = {
    0x1406: "雷柏 VT3S",
    0x1410: "雷柏 VT3S",
    0x4606: "雷柏 VT3S（有线）",
    0x1411: "雷柏 VT3S",
    0x4611: "雷柏 VT3S（有线）",
    0x1460: "雷柏 VT7",
    0x4660: "雷柏 VT7（有线）",
    0x1412: "雷柏 VT3",
    0x4612: "雷柏 VT3（有线）",
    0x1417: "雷柏 VT3 MAX",
    0x4617: "雷柏 VT3 MAX（有线）",
}
"""来自 rapoo-tray 的**实测**机型表。VT3S V2 若不在表内，会走"通用"分支，
靠 VID + 接口角色自动识别，功能同样可用。"""


class DeviceError(Exception):
    """与鼠标通信失败。"""


@dataclass
class DeviceInfo:
    """一只已连接的鼠标。"""

    product_id: int
    product_string: str
    paths: Dict[str, object] = field(default_factory=dict)

    @property
    def model(self) -> str:
        return MODEL_NAMES.get(self.product_id, self.product_string or "雷柏二代鼠标")

    @property
    def wired(self) -> bool:
        """有线机型 PID 以 ``46`` 开头（见 rapoo-tray 的实测规律）。"""
        return f"{self.product_id:04x}".startswith("46")

    def __str__(self) -> str:
        link = "有线" if self.wired else "2.4G"
        return f"{self.model} [pid=0x{self.product_id:04x}, {link}]"


def _role_for(usage_page, usage) -> Optional[str]:
    if usage_page != VENDOR_USAGE_PAGE:
        return None
    return {
        USAGE_CONTROL: "control",
        USAGE_FEATURE: "feature",
        USAGE_STATUS: "status",
    }.get(usage)


def _assign_roles(infos: List[dict]) -> Dict[str, object]:
    """把同一 PID 下的接口映射成 control/feature/status。

    优先用 usage 精确匹配；拿不到 usage 时按路径排序退化为顺序分配，
    保证功能仍可跑通（顺序分配在实测机型上稳定的前提是接口数量固定为 3）。
    """
    vendor_infos = [i for i in infos if i.get("usage_page") in (VENDOR_USAGE_PAGE, 0)]
    roles: Dict[str, object] = {}
    for info in vendor_infos:
        role = _role_for(info.get("usage_page"), info.get("usage"))
        if role and role not in roles:
            roles[role] = info.get("path")
    if "control" in roles and "feature" in roles:
        return roles

    ordered = sorted(vendor_infos, key=lambda i: str(i.get("path")))
    if len(ordered) >= 3:
        roles.setdefault("control", ordered[0].get("path"))
        roles.setdefault("feature", ordered[1].get("path"))
        roles.setdefault("status", ordered[2].get("path"))
    return roles


def enumerate_devices(vendor_id: int = p.VENDOR_ID) -> List[DeviceInfo]:
    """枚举当前连接的雷柏二代鼠标。"""
    if not hidapi_available():
        raise RuntimeError("未安装 hidapi 绑定（pip install hid），无法枚举设备")
    import hid  # 局部 import，避免离线环境硬依赖

    grouped: Dict[int, List[dict]] = {}
    for info in hid.enumerate(vendor_id):
        grouped.setdefault(info["product_id"], []).append(info)

    devices: List[DeviceInfo] = []
    for product_id, infos in sorted(grouped.items()):
        roles = _assign_roles(infos)
        if "control" not in roles or "feature" not in roles:
            continue  # 不是配置接口，跳过
        devices.append(
            DeviceInfo(
                product_id=product_id,
                product_string=(infos[0].get("product_string") or "").strip(),
                paths=roles,
            )
        )
    return devices


def first_device(vendor_id: int = p.VENDOR_ID) -> Optional[DeviceInfo]:
    devices = enumerate_devices(vendor_id)
    return devices[0] if devices else None


class Session:
    """一条已打开的连接。

    雷柏二代的写操作没有独立应答，因此 :meth:`write_register` 统一采用
    "写后回读校验"，回读不一致就重发，这也是社区驱动的一致做法。
    """

    SETTLE_S = 0.02
    """每次下发命令后等待设备处理的时间。"""
    RETRIES = 3

    def __init__(self, transport: Transport):
        self.transport = transport
        self._battery_cache: Optional[int] = None

    # -- 生命周期 ---------------------------------------------------------
    def __enter__(self) -> "Session":
        self.unlock()
        return self

    def __exit__(self, *exc) -> None:
        self.transport.close()

    def unlock(self) -> None:
        self.transport.write_control(p.build_unlock())
        time.sleep(self.SETTLE_S)

    # -- 寄存器读写 -------------------------------------------------------
    def read_raw(self, bank: int, addr: int, length: int) -> bytes:
        last_error: Optional[str] = None
        for _ in range(self.RETRIES):
            self.transport.write_control(p.build_read(bank, addr, length))
            time.sleep(self.SETTLE_S)
            try:
                data = p.parse_feature_read(self.transport.get_feature(), length)
            except Exception as exc:  # noqa: BLE001 - 句柄异常也要重试
                last_error = str(exc)
                data = None
            if data is not None:
                return data
        raise DeviceError(
            f"读取 0x{bank:02x}/0x{addr:02x} 失败（{last_error or '应答超时'}）"
        )

    def write_raw(self, bank: int, addr: int, data: bytes) -> None:
        data = bytes(data)
        for _ in range(self.RETRIES):
            self.transport.write_control(p.build_write(bank, addr, data))
            time.sleep(self.SETTLE_S)
            try:
                back = p.parse_feature_read(self.transport.get_feature(), len(data))
            except Exception:  # noqa: BLE001
                back = None
            if back is None:
                # 部分机型写命令不走 Feature 应答，回读一次确认
                try:
                    back = self.read_raw(bank, addr, len(data))
                except DeviceError:
                    continue
            if back == data:
                return
        raise DeviceError(
            f"写入 0x{bank:02x}/0x{addr:02x} 校验失败：期望 {data.hex()}，回读 {back.hex() if back else '无'}"
        )

    def read_register(self, reg: p.Register) -> bytes:
        return self.read_raw(reg.bank, reg.addr, reg.length)

    def write_register(self, reg: p.Register, data: bytes) -> None:
        self.write_raw(reg.bank, reg.addr, data)

    # -- 状态广播 ---------------------------------------------------------
    def read_status(self, timeout_ms: int = 200) -> Optional[p.StatusReport]:
        buf = self.transport.read_status(timeout_ms)
        if not buf:
            return None
        report = p.parse_status(buf, self._battery_cache)
        if report is not None and 2 <= report.battery <= 100:
            self._battery_cache = report.battery
        return report

    def wait_status(self, timeout_s: float = 2.0, poll_ms: int = 100) -> Optional[p.StatusReport]:
        """等一帧状态广播。设备是主动推送的，因此这里只是个短轮询。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            report = self.read_status(poll_ms)
            if report is not None:
                return report
        return None


def open_session(device: DeviceInfo) -> Session:
    """打开设备的三接口通道并返回会话。"""
    transport = HidapiTransport(
        control_path=device.paths["control"],
        feature_path=device.paths["feature"],
        status_path=device.paths.get("status"),
    )
    return Session(transport)
