"""配置快照：把一组寄存器值存成 JSON，并在需要时整组下发给鼠标。

快照里保存的是**原始字节的十六进制**——协议语义未完全确认的寄存器也能安全地
原样备份与还原，不会因为我们的解码理解有误而写坏设备。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import protocol as p
from .config import snapshots_dir
from .device import DeviceError, Session

SCHEMA_VERSION = 1


@dataclass
class Snapshot:
    """一只鼠标在某一时刻的寄存器集合。"""

    name: str
    product_id: int
    registers: Dict[str, str] = field(default_factory=dict)
    """``"bank:addr" -> 十六进制字节``，例如 ``"08:80": "01"``。"""
    created: str = ""
    schema: int = SCHEMA_VERSION

    @property
    def path(self) -> Path:
        return snapshots_dir() / f"{self.name}.json"

    @property
    def size(self) -> int:
        return len(self.registers)

    def describe(self) -> str:
        return (
            f"{self.name}（{self.size} 项，pid=0x{self.product_id:04x}"
            f"{'，' + self.created if self.created else ''}）"
        )


def _split(key: str) -> tuple:
    bank, addr = key.split(":", 1)
    return int(bank, 16), int(addr, 16)


def key_of(bank: int, addr: int) -> str:
    return f"{bank:02x}:{addr:02x}"


def capture(
    session: Session,
    name: str,
    product_id: int,
    registers: Iterable[p.Register] = p.SYSTEM_REGISTERS,
) -> Snapshot:
    """从当前鼠标读出一组寄存器，生成快照对象（不落盘）。"""
    data: Dict[str, str] = {}
    for reg in registers:
        try:
            data[reg.key] = session.read_register(reg).hex()
        except DeviceError:
            continue  # 个别机型不支持某寄存器时跳过，不中断整次采集
    return Snapshot(
        name=name,
        product_id=product_id,
        registers=data,
        created=time.strftime("%Y-%m-%d %H:%M:%S"),
    )


def save(snapshot: Snapshot) -> Path:
    snapshot.path.write_text(
        json.dumps(asdict(snapshot), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return snapshot.path


def load(name: str) -> Snapshot:
    path = snapshots_dir() / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"快照不存在: {name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Snapshot(
        name=payload.get("name", name),
        product_id=int(payload.get("product_id", 0)),
        registers=dict(payload.get("registers", {})),
        created=payload.get("created", ""),
        schema=int(payload.get("schema", SCHEMA_VERSION)),
    )


def list_names() -> List[str]:
    return sorted(path.stem for path in snapshots_dir().glob("*.json"))


def delete(name: str) -> None:
    path = snapshots_dir() / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"快照不存在: {name}")
    path.unlink()


def apply(
    session: Session,
    snapshot: Snapshot,
    only: Optional[Iterable[str]] = None,
) -> List[str]:
    """把快照里的寄存器逐个写入鼠标，返回实际写成功的键列表。"""
    wanted = set(only) if only is not None else None
    written: List[str] = []
    for key, hex_value in snapshot.registers.items():
        if wanted is not None and key not in wanted:
            continue
        bank, addr = _split(key)
        session.write_raw(bank, addr, bytes.fromhex(hex_value))
        written.append(key)
    return written


def diff(session: Session, snapshot: Snapshot) -> Dict[str, tuple]:
    """比较鼠标当前值与快照，返回 ``{key: (当前hex, 快照hex)}``。"""
    changes: Dict[str, tuple] = {}
    for key, hex_value in snapshot.registers.items():
        bank, addr = _split(key)
        length = len(bytes.fromhex(hex_value))
        try:
            current = session.read_raw(bank, addr, length).hex()
        except DeviceError:
            current = "??"
        if current != hex_value:
            changes[key] = (current, hex_value)
    return changes


def display_name(key: str) -> str:
    """把 ``"08:80"`` 映射回人类可读的寄存器名（若有）。"""
    for reg in p.SYSTEM_REGISTERS:
        if reg.key == key:
            return reg.name
    return key
