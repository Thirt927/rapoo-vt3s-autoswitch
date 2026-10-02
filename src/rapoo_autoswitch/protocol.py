"""雷柏二代 HID 协议：报文构造与解析（Nordic 54L15 + PAW3950 系列，含 VT3S V2）。

协议常量与帧布局来自社区逆向成果，来源均为 MIT 许可项目（详见 docs/PROTOCOL.md）：

* ``Iris-0109/rapoo-tray``   (MIT) —— 三接口模型、状态广播报文、系统寄存器地址
* ``D3m0nZOnFire/mousectl``  (MIT) —— 银行/寄存器读写模型与"写后回读校验"策略
* ``Nuitfanee/ClickSync``    (GPL-2.0) —— 仅用于交叉验证，未复制其任何代码

本模块只做纯字节处理，不涉及任何 I/O，因此可以在没有鼠标的机器上完整测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

VENDOR_ID = 0x24AE
"""雷柏通用 USB Vendor ID。二代机型（VT3S / VT7 / VT3 MAX ...）都在此 VID 下。"""

# --- 报文 ID ---------------------------------------------------------------
RID_CONTROL = 0x06
"""控制接口（输出）。所有命令都从这里下发。"""
RID_STATUS = 0x07
"""状态接口（输入）。设备主动广播电量 / DPI / 连接方式，无需请求。"""
RID_FEATURE = 0x08
"""Feature 接口。命令的应答数据从这里读回。"""

# --- 命令字 ---------------------------------------------------------------
CMD_UNLOCK = 0xA3
"""解锁握手。进入可写状态前先发一次。"""
CMD_READ = 0xA4
CMD_WRITE = 0xA5

ACK_OK = 0x01
"""Feature 应答里的成功标志。"""

FRAME_SIZE = 33
"""命令报文总长（含 1 字节报文 ID）。"""
MAX_PAYLOAD = 25
"""单帧可携带的最大数据长度：33 - 8 字节头部。"""

# --- 存储银行 -------------------------------------------------------------
BANK_COMM = 0x00
BANK_BUTTON = 0x06
BANK_SYSTEM = 0x08
"""系统设置银行：DPI、回报率、休眠、LOD 等都在这里。

注意：这里指的是**第 0 组**板载配置的系统银行。设备实际有多组配置，
真正生效的是哪一组由 ``ADDR_ACTIVE_PROFILE`` 决定，见下。
"""

# --- 板载配置分组 ---------------------------------------------------------
BANK_PROFILE_INFO = 0x01
"""记录"当前选中哪一组板载配置"的银行。"""

ADDR_ACTIVE_PROFILE = 0x0C
"""``bank 0x01`` 偏移 ``0x0C``：当前生效的配置组号（0 起）。

实测（VT3S V2）：写 ``0`` 生效第 0 组、写 ``1`` 生效第 1 组，实时配置随之切换。
"""

PROFILE_STRIDE = 4
"""每组板载配置占 4 个 bank：按键 / 保留 / 系统 / 名字。

实测分组（VT3S V2）：

======  =========  =========  ===================
组      按键 bank  系统 bank  名字 bank（0xEE 起）
======  =========  =========  ===================
0       0x06       0x08       0x09（"CFG1"）
1       0x0A       0x0C       0x0D（"办公"）
======  =========  =========  ===================

**只有当前选中那一组会真正生效**——写其他组的 bank，回读校验会通过，但鼠标行为不变。
"""


def system_bank_for(profile_index: int) -> int:
    """第 N 组板载配置的系统 bank：0 → 0x08、1 → 0x0C、2 → 0x10 …"""
    return BANK_SYSTEM + PROFILE_STRIDE * profile_index


# --- DPI 相关寄存器地址（bank 0x08）----------------------------------------
ADDR_DPI_TABLE_X = 0x88
ADDR_DPI_TABLE_Y = 0xC8
ADDR_DPI_SLOT_COUNT = 0x96
ADDR_DPI_ACTIVE_INDEX = 0x98

ADDR_PERFORMANCE = 0xDC
PERFORMANCE_BYTES = 7
"""性能模式块（``0xDC..0xE2``，7 字节）。

对应 A-Hub 的「性能模式」（办公/均衡/火力/超核/竞技超核/狂暴竞技）。实测这块是
**随回报率联动**的复合结构，不是单个字节：同一回报率下切档只动 ``0xDE``；回报率
一变，``0xDC``/``0xDD``/``0xE2`` 也会跟着改。因此这里**不做语义解码**，按原始字节
整块存取（与快照"原样备份还原"的定位一致）。

注意：A-Hub 里回报率与性能模式是**联动限制**的——125/250Hz 只能用后两档、500Hz
最高火力、1000Hz 最低均衡、2000Hz 最低火力、4000/8000Hz 只能前 3 档。写入必须
整组一致，不要混搭不同回报率下的取值。
"""

PERFORMANCE_MODE_OFFSET = 0xDE - ADDR_PERFORMANCE
"""档位字节在块内的下标。同一回报率下切档只动这一个字节。"""

PERFORMANCE_MODE_NAMES: Dict[Tuple[int, int], str] = {
    # (回报率 Hz, 档位字节) -> A-Hub 里的档位名。
    # 同一个字节在不同回报率下含义不同：500Hz 的 0x02 是「火力」，而 8000Hz 的
    # 0x02 是「竞技超核」，所以必须连回报率一起查，不能只看字节。
    (500, 0x00): "办公",
    (500, 0x02): "火力",
    (8000, 0x02): "竞技超核",
}
"""性能模式档位名——**只登记实测确认过的组合**。

6 档依次是办公 / 均衡 / 火力 / 超核 / 竞技超核 / 狂暴竞技，但整块字节随回报率联动，
目前实测样本还很少（见 docs/PROTOCOL.md）。查不到时这里返回 ``None``，由调用方原样
显示字节：**宁可写"未知"，也不要猜错档位名**。
"""


def performance_mode_name(polling_hz: Optional[int], raw: bytes) -> Optional[str]:
    """按（回报率, 档位字节）查档位名；没实测过的组合返回 ``None``。"""
    if len(raw) <= PERFORMANCE_MODE_OFFSET:
        return None
    return PERFORMANCE_MODE_NAMES.get((polling_hz, raw[PERFORMANCE_MODE_OFFSET]))

DPI_MAX_SLOTS = 6
"""最多 6 档 DPI。"""

DPI_TABLE_BYTES = DPI_MAX_SLOTS * 2
"""DPI 表的完整读取长度：6 档 × u16。"""

DPI_SLOT_COUNT_KEY = f"{BANK_SYSTEM:02x}:{ADDR_DPI_SLOT_COUNT:02x}"
"""快照里档位数寄存器的键，下发 DPI 表时要用它决定表长。"""


# --------------------------------------------------------------------------
# 报文构造
# --------------------------------------------------------------------------
def build_unlock() -> bytes:
    """构造解锁握手报文 ``06 A5 A3 ...``。"""
    frame = bytearray(FRAME_SIZE)
    frame[0] = RID_CONTROL
    frame[1] = CMD_WRITE
    frame[2] = CMD_UNLOCK
    return bytes(frame)


def build_read(bank: int, addr: int, length: int) -> bytes:
    """构造读寄存器报文 ``06 A5 A4 <len> <addr> <bank> ...``。"""
    if not 1 <= length <= MAX_PAYLOAD:
        raise ValueError(f"读取长度必须在 1..{MAX_PAYLOAD} 之间，收到 {length}")
    frame = bytearray(FRAME_SIZE)
    frame[0] = RID_CONTROL
    frame[1] = CMD_WRITE
    frame[2] = CMD_READ
    frame[3] = length
    frame[4] = addr
    frame[5] = bank
    return bytes(frame)


def build_write(bank: int, addr: int, data: bytes) -> bytes:
    """构造写寄存器报文 ``06 A5 A5 <len> <addr> <bank> <data...>``。"""
    data = bytes(data)
    if not 1 <= len(data) <= MAX_PAYLOAD:
        raise ValueError(f"写入长度必须在 1..{MAX_PAYLOAD} 之间，收到 {len(data)}")
    frame = bytearray(FRAME_SIZE)
    frame[0] = RID_CONTROL
    frame[1] = CMD_WRITE
    frame[2] = CMD_WRITE
    frame[3] = len(data)
    frame[4] = addr
    frame[5] = bank
    frame[8:8 + len(data)] = data
    return bytes(frame)


def parse_feature_read(buf: bytes, expected_length: int) -> Optional[bytes]:
    """从 Feature 应答里取出 ``expected_length`` 字节数据。

    Windows 的 HID 驱动可能保留也可能剥掉报文 ID，因此两种布局都要认：

    * ``buf[0] == ACK_OK``        -> 数据从偏移 4 开始
    * ``buf[0] == RID_FEATURE``   -> 数据从偏移 5 开始
    """
    buf = bytes(buf)
    if len(buf) >= 4 + expected_length and buf[0] == ACK_OK:
        return buf[4:4 + expected_length]
    if len(buf) >= 5 + expected_length and buf[0] == RID_FEATURE and buf[1] == ACK_OK:
        return buf[5:5 + expected_length]
    return None


# --------------------------------------------------------------------------
# 状态广播报文
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class StatusReport:
    """状态接口主动广播的一帧。"""

    wired: bool
    """True = 有线直连，False = 2.4G 接收器。"""
    dpi_level: int
    """当前 DPI 档位，1 起。"""
    dpi_x: int
    dpi_y: int
    charging: bool
    battery: int
    """电量百分比 0..100。"""
    status_byte: int
    """原始状态位，充电/低电等标志的出处。"""


def parse_status(buf: bytes, cached_battery: Optional[int] = None) -> Optional[StatusReport]:
    """解析状态广播报文；不是状态报文时返回 ``None``。

    设备标记（``buf[1]`` 高半字节）说明连接方式：``0x10`` 有线，``0x20`` 2.4G。

    刚插上线时 ADC 未稳定，电量可能短暂读到 0/1；此时沿用 ``cached_battery``，
    避免电量图标瞬间跳到 0。
    """
    buf = bytes(buf)
    if len(buf) < 9 or buf[0] != RID_STATUS:
        return None
    mode_nibble = buf[1] & 0xF0
    if mode_nibble not in (0x10, 0x20):
        return None

    status_byte = buf[7]
    raw_battery = buf[8]
    extra = buf[9] if len(buf) > 9 else 0

    charging = bool(
        status_byte & 0x02
        or status_byte in (0x02, 0x03)
        or extra in (0x01, 0x02)
    )

    if 2 <= raw_battery <= 100:
        battery = raw_battery
    elif raw_battery <= 1:
        if cached_battery is not None and 2 <= cached_battery <= 100:
            battery = cached_battery
        else:
            battery = 100 if raw_battery == 0 else raw_battery
    else:
        battery = 100

    return StatusReport(
        wired=mode_nibble == 0x10,
        dpi_level=buf[2] + 1,
        dpi_x=buf[3] | (buf[4] << 8),
        dpi_y=buf[5] | (buf[6] << 8),
        charging=charging,
        battery=battery,
        status_byte=status_byte,
    )


# --------------------------------------------------------------------------
# 寄存器表
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Register:
    """一个可读写的系统寄存器。"""

    bank: int
    addr: int
    name: str
    length: int = 1
    kind: str = "byte"
    """取值语义：``byte`` / ``polling`` / ``bool`` / ``minutes`` / ``angle`` /
    ``dpi_table`` / ``slot_count`` / ``dpi_index``。"""
    help: str = ""
    order: int = 100
    """下发顺序，小的先写。协议有依赖关系时必须显式指定。"""

    @property
    def key(self) -> str:
        """快照里用的稳定标识，例如 ``08:80``。"""
        return f"{self.bank:02x}:{self.addr:02x}"

    def decode(self, raw: bytes) -> object:
        if self.kind == "dpi_table":
            return [
                int.from_bytes(raw[i * 2:i * 2 + 2], "little")
                for i in range(len(raw) // 2)
            ]
        if self.kind == "blob":
            return bytes(raw)
        value = raw[0] if self.length == 1 else raw
        if self.kind == "polling":
            return POLLING_CODE_TO_HZ.get(value, value)
        if self.kind == "bool":
            return bool(value)
        if self.kind == "angle":
            return int.from_bytes(bytes([value]), "little", signed=True)
        if self.kind == "slot_count":
            # 协议码 = 档位数 - 1
            return min(value + 1, DPI_MAX_SLOTS)
        if self.kind == "dpi_index":
            # 显示用的 1 起档位号
            return value + 1
        return value

    def encode(self, value) -> bytes:
        if self.kind == "dpi_table":
            values = value
            if isinstance(value, str):
                values = [int(part) for part in value.replace(",", " ").split()]
            return b"".join(int(v).to_bytes(2, "little") for v in values)
        if self.kind == "blob":
            # 十六进制串（可带空格）或原始字节，长度必须正好
            if isinstance(value, str):
                return bytes.fromhex(value.replace(" ", "").replace(":", ""))
            return bytes(value)
        if self.kind == "polling":
            hz = int(value)
            if hz not in POLLING_HZ_TO_CODE:
                raise ValueError(f"不支持的回报率 {hz} Hz")
            value = POLLING_HZ_TO_CODE[hz]
        elif self.kind == "bool":
            value = 1 if value else 0
        elif self.kind in ("slot_count", "dpi_index"):
            # 对外是 1 起，落盘是 0 起的协议码 / 索引
            value = int(value) - 1
        if self.length == 1:
            return bytes([int(value) & 0xFF])
        return int(value).to_bytes(self.length, "little", signed=self.kind == "angle")


POLLING_HZ_TO_CODE: Dict[int, int] = {
    125: 0x08,
    250: 0x04,
    500: 0x02,
    1000: 0x01,
    2000: 0x84,
    4000: 0x82,
    8000: 0x81,
}
POLLING_CODE_TO_HZ: Dict[int, int] = {v: k for k, v in POLLING_HZ_TO_CODE.items()}


SYSTEM_REGISTERS: List[Register] = [
    # --- DPI：协议要求先写两张表，再写档位数，最后写当前档位 ---
    Register(
        BANK_SYSTEM, ADDR_DPI_TABLE_X, "dpi_table_x", length=DPI_TABLE_BYTES,
        kind="dpi_table", order=10, help="X 轴各档 DPI（u16le × 档位数）",
    ),
    Register(
        BANK_SYSTEM, ADDR_DPI_TABLE_Y, "dpi_table_y", length=DPI_TABLE_BYTES,
        kind="dpi_table", order=11, help="Y 轴各档 DPI（u16le × 档位数）",
    ),
    Register(
        BANK_SYSTEM, ADDR_DPI_SLOT_COUNT, "dpi_slot_count", kind="slot_count",
        order=20, help="启用的 DPI 档位数（1..6）",
    ),
    Register(
        BANK_SYSTEM, ADDR_DPI_ACTIVE_INDEX, "dpi_active_index", kind="dpi_index",
        order=21, help="当前生效的档位（1..档位数）",
    ),
    # --- 性能与时间 ---
    Register(BANK_SYSTEM, 0x80, "polling_hz", kind="polling", order=40, help="回报率"),
    Register(BANK_SYSTEM, 0x81, "key_scan_rate", order=41, help="按键扫描率"),
    Register(BANK_SYSTEM, 0x84, "lod", order=42, help="抬升高度（LOD）档位"),
    Register(BANK_SYSTEM, 0x85, "motion_sync", kind="bool", order=43, help="移动同步"),
    Register(BANK_SYSTEM, 0xC0, "debounce_ms", order=44, help="按键消抖时间"),
    Register(BANK_SYSTEM, 0xC1, "lift_delay_ms", order=45, help="抬起延迟"),
    Register(
        BANK_SYSTEM, 0xC2, "sleep_minutes", kind="minutes", order=46,
        help="休眠时间（2..120 分钟）",
    ),
    Register(
        BANK_SYSTEM, 0xC3, "linear_ripple", order=47,
        help="bit0 直线修正、bit1 波纹控制（0=开启）",
    ),
    Register(BANK_SYSTEM, 0xC4, "sensor_angle", kind="angle", order=48, help="传感器角度"),
    Register(BANK_SYSTEM, 0xC5, "glass_mode", kind="bool", order=49, help="玻璃模式"),
    # --- 性能模式（A-Hub 里的"扫描率"档位）---
    # 必须排在回报率（0x80，order=40）之后：固件会按回报率重排这一块，
    # 先写回报率再写它，才不会互相覆盖。
    Register(
        BANK_SYSTEM, ADDR_PERFORMANCE, "performance_mode", length=PERFORMANCE_BYTES,
        kind="blob", order=50,
        help="性能模式块 0xDC..0xE2（随回报率联动，按原始字节存取）",
    ),
]
"""快照默认覆盖的系统寄存器。

下发时会按 ``Register.order`` 排序：DPI 表先于档位数/当前档位，性能模式块排在
回报率之后。未实测机型请先 ``probe`` 确认后再写入。
"""

REGISTRY: Dict[str, Register] = {r.name: r for r in SYSTEM_REGISTERS}
"""按名字索引，供 CLI 使用。"""

REGISTRY_BY_KEY: Dict[str, Register] = {r.key: r for r in SYSTEM_REGISTERS}
"""按 ``"bank:addr"`` 索引，供快照下发时查顺序与类型。"""
