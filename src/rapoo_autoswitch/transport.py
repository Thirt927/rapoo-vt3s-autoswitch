"""HID 传输层。

真实实现基于 ``hidapi``（PyPI 包名 ``hid``）：雷柏二代会暴露三个 HID 接口，
分别负责下发命令、读回 Feature 应答、接收状态广播，因此 :class:`HidapiTransport`
同时持有三个句柄。

``hid`` 是可选依赖：只做离线逻辑/单元测试的机器不装它也能 import 本模块。
"""

from __future__ import annotations

from typing import Optional

from .protocol import RID_FEATURE

try:  # pragma: no cover - 取决于运行环境
    import hid as _hidapi
except Exception:  # noqa: BLE001 - hidapi 缺失或底层库加载失败都要降级
    _hidapi = None  # type: ignore[assignment]


def hidapi_available() -> bool:
    """当前环境是否能真正访问 HID 设备。"""
    return _hidapi is not None


class Transport:
    """一次设备连接的字节通道。"""

    def write_control(self, frame: bytes) -> None:
        raise NotImplementedError

    def get_feature(self, length: int = 33) -> bytes:
        raise NotImplementedError

    def read_status(self, timeout_ms: int = 200) -> Optional[bytes]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class HidapiTransport(Transport):
    """基于 hidapi 的三接口实现。"""

    def __init__(self, control_path, feature_path, status_path=None):
        if _hidapi is None:
            raise RuntimeError(
                "未安装 hidapi 绑定，无法连接鼠标。请先执行: pip install hid"
            )
        self._control = self._open(control_path)
        self._feature = self._open(feature_path)
        self._status = self._open(status_path) if status_path else None

    @staticmethod
    def _open(path):
        device = _hidapi.device()
        if not device.open_path(path):
            raise RuntimeError(f"无法打开 HID 接口: {path!r}")
        return device

    def write_control(self, frame: bytes) -> None:
        self._control.write(bytes(frame))

    def get_feature(self, length: int = 33) -> bytes:
        return bytes(self._feature.get_feature_report(RID_FEATURE, length))

    def read_status(self, timeout_ms: int = 200) -> Optional[bytes]:
        if self._status is None:
            return None
        buf = self._status.read(64, timeout_ms)
        return bytes(buf) if buf else None

    def close(self) -> None:
        for device in (self._control, self._feature, self._status):
            if device is not None:
                try:
                    device.close()
                except Exception:  # noqa: BLE001 - 关闭失败不影响退出
                    pass
