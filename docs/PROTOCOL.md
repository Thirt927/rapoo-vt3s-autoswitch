# 雷柏二代 HID 协议笔记

本文记录 `rapoo-autoswitch` 所依赖的协议细节、它们的来源，以及**尚未验证**的部分。
凡标注"未验证"的，请不要当成事实直接依赖；真机确认后欢迎提 PR 修正。

## 1. 适用范围

- 厂商：雷柏 Rapoo，VID `0x24AE`
- 架构：二代 Nordic 54L15 + PAW3950 系列（VT3S / VT3S V2 / VT7 / VT3 / VT3 MAX …）
- 不适用：更早的 EEPROM 型机型（如 VT3 PRO）：那套用报文 ID `0xBA` + `0xA4/0xA5`，
  32 字节 A4/A5 帧，与本协议无关。

## 2. 三接口模型

同一 VID/PID 下雷柏二代暴露 3 个 HID 接口，靠 `usage_page = 0xFF00` 和 `usage` 区分：

| 角色 | usage | 报文 ID | 用途 |
| --- | --- | --- | --- |
| control | `0x000E` | `0x06` | 主机 → 设备，下发命令 |
| feature | `0x000F` | `0x08` | 设备 → 主机，回读命令应答 |
| status | `0x0002` | `0x07` | 设备 → 主机，主动广播电量/DPI |

*来源：rapoo-tray `device_manager.cpp`（按 `HidP_GetCaps` 的 usage 与报告长度选择端点）。*

> 部分平台在枚举时拿不到 `usage`，此时本项目退化为"同一 PID 下按接口路径排序，
> 依次当作 control / feature / status"。这是权宜之计，若设备接口数量不固定需改回
> usage 判定。

## 3. 命令报文（33 字节，control 接口）

第 0 字节是报文 ID `0x06`，其后为命令体：

```
偏移  0     1     2      3        4      5      6 7     8..32
     RID   A5    CMD    LEN      ADDR   BANK   00 00   DATA...
     0x06              /0xA4
                      /0xA5
```

| 命令 | 帧 | 说明 |
| --- | --- | --- |
| 解锁 | `06 A5 A3 00 00 00 00 00 00 …` | 进入可写状态前的握手 |
| 读   | `06 A5 A4 <len> <addr> <bank> 00 00 …` | 读 `len` 字节 |
| 写   | `06 A5 A5 <len> <addr> <bank> 00 00 <data…>` | 写 `len` 字节 |

- 单帧数据上限 25 字节（33 − 8 字节头部）。
- `A5`（偏移 1）在读写命令里都是固定值，第二个字节才是真正区分读写的命令字。

*来源：rapoo-tray `rapoo_protocol.cpp` 的 `BuildUnlockPacket` / `BuildReadCommand` /
`BuildWriteCommand`（MIT）。*

## 4. 应答读取（feature 接口）

设备把应答放在 Feature 报告 `0x08` 里。Windows 的 HID 驱动**可能保留也可能剥掉**
报文 ID，所以两种布局都要认：

| 布局 | 偏移 0 | 偏移 1 | 数据起始 |
| --- | --- | --- | --- |
| 无报文 ID | `0x01` (ACK) | — | 4 |
| 有报文 ID | `0x08` | `0x01` (ACK) | 5 |

`0x01` 表示成功；握手/处理中会读到 `0x02`（busy），此时应稍后重读。

*来源：rapoo-tray `ParseFeatureRead`（MIT）。*

### 时序

rapoo-tray 的做法是：写命令 → `Sleep(20)` → `HidD_GetFeature`。本项目取
`SETTLE_S = 0.02`，失败重试 3 次。

**未验证**：写命令是否也会在 feature 接口给出应答。本项目保守地采用"写后回读校验"：
写完再读一次比对，不一致就重发（`Session.write_raw`）。这也是 mousectl 的做法。

## 5. 存储银行

| 银行 | 名称 | 内容 |
| --- | --- | --- |
| `0x00` | COMM | 通信协议 |
| `0x06` | BUTTON | 按键映射 |
| `0x08` | SYSTEM | DPI、回报率、休眠、LOD 等系统设置 |

## 6. 系统银行寄存器（bank `0x08`）

| 地址 | 名称 | 长度 | 语义 | 状态 |
| --- | --- | --- | --- | --- |
| `0x88` | `dpi_table_x` | 变长 | X 轴各档 DPI，u16le × 档位数 | 已实现 |
| `0xC8` | `dpi_table_y` | 变长 | Y 轴各档 DPI | 已实现 |
| `0x96` | `dpi_slot_count` | 1 | 档位数，存的是 `档位数 − 1` | 已实现 |
| `0x98` | `dpi_active_index` | 1 | 当前档位索引，0 起 | 已实现 |
| `0x80` | `polling_hz` | 1 | 回报率码（见下表） | 已实现 |
| `0x81` | `key_scan_rate` | 1 | 按键扫描率 | 地址已知，语义未验证 |
| `0x84` | `lod` | 1 | 抬升高度档位 | 已实现 |
| `0x85` | `motion_sync` | 1 | 移动同步 0/1 | 已实现 |
| `0xC0` | `debounce_ms` | 1 | 按键消抖时间 | 地址已知，语义未验证 |
| `0xC1` | `lift_delay_ms` | 1 | 抬起延迟 | 地址已知，语义未验证 |
| `0xC2` | `sleep_minutes` | 1 | 休眠 2..120 分钟 | 地址已知，范围来自说明 |
| `0xC3` | `linear_ripple` | 1 | bit0 直线修正、bit1 波纹（0 = 开启） | 已实现 |
| `0xC4` | `sensor_angle` | 1 | 传感器角度，有符号 | 已实现 |
| `0xC5` | `glass_mode` | 1 | 玻璃模式 0/1 | 已实现 |

**未纳入快照**（写入可能影响通信，需自行用 `probe` 探索后再扩展）：
`0x60`（bank `0x00`，通信协议）、`0xD8`（无线策略 / LED 低电提示）、
`0xDC..0xE2`（按回报率动态选择的性能模式寄存器）。

### DPI 的下发顺序与变长表（重要）

DPI 不是一个寄存器，而是一组有依赖关系的寄存器，协议对写入**顺序**和**长度**都有要求：

1. 先写表 X（`0x88`）与表 Y（`0xC8`）；
2. 再写档位数（`0x96`）；
3. 最后写当前档位（`0x98`）。

表的数据长度必须是 `档位数 × 2` 字节，而不是固定的 12 字节——档位数为 3 时只写 6 字节。
顺序错了或长度不对，设备会按旧的档位数去解释表内容，表现为"DPI 设置不生效"。

本项目用 `Register.order` 表达这个依赖（表 10/11 → 档位数 20 → 当前档位 21），并在下发
DPI 表时按快照里的档位数裁剪长度；`tests/test_profiles.py` 中有对应测试锁住这两个约束。

*来源：ClickSync `ADDR.dpiTableA/dpiTableB/currentSlotCount/currentDpiIndex`、
`slotCountCode`、`dpiIndexU8` 与 `SPEC.dpiProfile.plan()` 的写入序列。*

### 其它已知地址（来自 ClickSync，未纳入快照）

| 地址 | 名称 | 说明 |
| --- | --- | --- |
| `0xC0` | `debounceMs` | 按键消抖 |
| `0xC1` | `liftDelayMs` | 抬起延迟 |
| `0xD8` | 无线策略 / LED 低电提示 | 一个地址两种用途，写入有风险 |
| `0xDC`..`0xE2` | 性能模式 | 按回报率动态选择地址 |
| `0x60`（bank `0x00`） | `commProtocol` | 通信协议类型 |
| bank `0x06` | 按键映射 | `BUTTON_ADDR`，键值编码为 4 字节 [{funckey, keycode}] |

按键映射（bank `0x06`）尚未纳入本项目，因为键值编码仍有一层未验证的细节；需要时可按
rapoo-tray / ClickSync 的 `BUTTON_ADDR` 与 `keymapAction` 继续探索。

### 回报率码表（`0x80`）

| 码 | Hz | 码 | Hz |
| --- | --- | --- | --- |
| `0x08` | 125 | `0x84` | 2000 |
| `0x04` | 250 | `0x82` | 4000 |
| `0x02` | 500 | `0x81` | 8000 |
| `0x01` | 1000 | | |

*来源：rapoo-tray `CodeToPollingHz` / `PollingHzToCode`（MIT）。*

## 7. 状态广播报文（status 接口，报文 ID `0x07`）

| 偏移 | 含义 |
| --- | --- |
| 0 | 报文 ID `0x07` |
| 1 | 设备标记：高半字节 `0x10` = 有线直连，`0x20` = 2.4G 接收器 |
| 2 | 当前 DPI 档位 − 1（即 0 表示第 1 档） |
| 3..4 | DPI X，小端 u16 |
| 5..6 | DPI Y，小端 u16 |
| 7 | 状态位；`& 0x02` 置位表示充电中 |
| 8 | 电量百分比 0..100 |
| 9 | 附加标志（`0x01`/`0x02` 也视为充电中） |

电量保护：刚插上线时 ADC 未稳定，会短暂读到 0/1。此时沿用上一次的有效读数
（本项目在 `Session` 里缓存），避免电量图标瞬间掉到 0。

*来源：rapoo-tray `ParseStatusReport`（MIT）。*

## 8. 机型 PID（已实测）

| PID | 型号 | 连接 |
| --- | --- | --- |
| `1406` / `1410` / `1411` | VT3S | 2.4G |
| `4606` / `4611` | VT3S | 有线 |
| `1460` / `4660` | VT7 | 2.4G / 有线 |
| `1412` / `4612` | VT3 / VT3 | 2.4G / 有线 |
| `1417` / `4617` | VT3 MAX | 2.4G / 有线 |

规律（**未验证**）：有线机型 PID 以 `46` 开头。本项目据此推断"有线 / 2.4G"，
并以状态广播里的设备标记为准。

*来源：rapoo-tray `VERIFIED_MODELS`（MIT，均标注"实测已验证"）。*

## 9. VT3S V2 验证清单

拿到真机后，建议按顺序确认并把结论回填到本文与 `protocol.SYSTEM_REGISTERS`：

1. `rapoo-autoswitch list` —— 确认三个接口都被正确识别为 control/feature/status，
   记录实际 PID 与产品名；
2. `rapoo-autoswitch status` —— 核对电量、DPI 档位/数值、连接方式是否与官方软件一致；
3. `rapoo-autoswitch probe 0x08 0x80 1` —— 只读验证握手与应答路径是否打通；
4. `rapoo-autoswitch snapshot 出厂备份` —— 先留一份可回滚的原始快照；
5. 用 `get` / `set` 逐个验证 `0x80 / 0x84 / 0x85 / 0xC2 / 0xC3 / 0xC4 / 0xC5` 的
   语义，与官方软件界面比对；
6. 用 `rapoo-autoswitch dpi` 核对各档 DPI 与当前档位是否与官方软件一致，并验证
   "改档位数后 DPI 是否真的生效"（顺序/长度写错时的典型症状就是这里不生效）；
7. 用 `probe` 扫描 `0x08` 银行 `0x90..0xA0`、`0xC0..0xD0`、`0xD8..0xE2` 区间，
   补齐按键映射（bank `0x06`）与性能模式寄存器；
8. 确认写入后**断开重连**配置是否保持（判断写的是 RAM 还是板载 Flash）。

## 10. 参考项目与许可

| 项目 | 许可 | 本项目如何使用 |
| --- | --- | --- |
| [Iris-0109/rapoo-tray](https://github.com/Iris-0109/rapoo-tray) | MIT | 移植三接口模型、状态报文布局、寄存器地址、机型表 |
| [D3m0nZOnFire/mousectl](https://github.com/D3m0nZOnFire/mousectl) | MIT | 移植银行/寄存器读写与写后回读校验策略 |
| [Nuitfanee/ClickSync](https://github.com/Nuitfanee/ClickSync) | GPL-2.0 | **仅提取协议事实**：DPI 表地址、档位数/档位索引编码、写入序列。未复制任何代码 |

[MIT 许可](https://opensource.org/license/mit)允许在保留版权声明的前提下使用与再分发，
本项目因此在 `LICENSE` 中保留自有版权声明，并在上表与 README 中明确致谢。

GPL-2.0 的 ClickSync 与 MIT 许可不兼容，因此本项目**没有**引入它的任何代码、注释或命名，
只从中读取了互操作性所必需的**功能性事实**（寄存器地址、编码规则、写入顺序）。这也是
各品牌第三方驱动（如 mousectl 对 Rapoo、Solaar 对 Logitech）普遍采用的做法。
