"""测试用的假设备：在内存里模拟雷柏二代的三接口行为。"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from rapoo_autoswitch import protocol as p


class FakeTransport:
    """内存版 HID 通道，行为对齐真实固件：

    * 读命令（0xA4）返回对应寄存器的当前值
    * 写命令（0xA5）写入寄存器并原样应答
    * 状态广播（0x07）从预设队列里取
    """

    def __init__(
        self,
        registers: Optional[Dict[Tuple[int, int], bytes]] = None,
        status_frames: Optional[List[bytes]] = None,
    ):
        self.registers: Dict[Tuple[int, int], bytearray] = {
            key: bytearray(value) for key, value in (registers or {}).items()
        }
        self.control_frames: List[bytes] = []
        self.status_frames: List[bytes] = list(status_frames or [])
        self.closed = False
        self._last_feature = p.RID_FEATURE.to_bytes(1, "little") + b"\x00" * 32

    # -- Transport 接口 ---------------------------------------------------
    def write_control(self, frame: bytes) -> None:
        frame = bytes(frame)
        self.control_frames.append(frame)
        command = frame[2]
        if command == p.CMD_UNLOCK:
            return
        length, addr, bank = frame[3], frame[4], frame[5]
        key = (bank, addr)
        if command == p.CMD_READ:
            # 真实设备总是按请求长度应答，短的内容补零
            stored = bytes(self.registers.get(key, b""))
            data = stored.ljust(length, b"\x00")[:length]
            self._last_feature = self._feature(data)
        elif command == p.CMD_WRITE:
            data = frame[8:8 + length]
            self.registers[key] = bytearray(data)
            # 真机不会回显写入的数据：写命令只回一个 ACK，其后跟零字节。
            # 这里必须照实现，否则会掩盖"误把写应答当校验结果"这类 bug
            # （真机上表现为每次写入都校验失败，虽然设备其实写成功了）。
            self._last_feature = self._feature(bytes(length))

    def get_feature(self, length: int = 33) -> bytes:
        return self._last_feature.ljust(length, b"\x00")[:length]

    def read_status(self, timeout_ms: int = 200) -> Optional[bytes]:
        if self.status_frames:
            return self.status_frames.pop(0)
        return None

    def close(self) -> None:
        self.closed = True

    # -- 工具 -------------------------------------------------------------
    @staticmethod
    def _feature(data: bytes) -> bytes:
        # 布局：08 01 00 00 00 <data...>，数据从偏移 5 开始
        return bytes([p.RID_FEATURE, p.ACK_OK, 0x00, 0x00, 0x00]) + bytes(data)

    def writes_to(self, bank: int, addr: int) -> List[bytes]:
        """某寄存器收到的所有写入数据。"""
        out: List[bytes] = []
        for frame in self.control_frames:
            if frame[2] == p.CMD_WRITE and frame[4] == addr and frame[5] == bank:
                length = frame[3]
                out.append(bytes(frame[8:8 + length]))
        return out


def status_frame(
    dpi_level: int = 1,
    dpi_x: int = 800,
    dpi_y: int = 800,
    battery: int = 57,
    wired: bool = False,
    charging: bool = False,
) -> bytes:
    """构造一帧状态广播。"""
    buf = bytearray(10)
    buf[0] = p.RID_STATUS
    buf[1] = 0x10 if wired else 0x20
    buf[2] = dpi_level - 1
    buf[3:5] = dpi_x.to_bytes(2, "little")
    buf[5:7] = dpi_y.to_bytes(2, "little")
    buf[7] = 0x02 if charging else 0x00
    buf[8] = battery
    return bytes(buf)
