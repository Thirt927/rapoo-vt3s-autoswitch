# rapoo-vt3s-autoswitch

按**电脑**自动切换雷柏二代鼠标配置的命令行工具 + 后台守护。

官方 A-Hub 里能存「出厂配置 / 办公 / 游戏」等好几套配置，但它不会根据你把鼠标插在
**哪台电脑**上自动切换。本项目在每台电脑上各存一份配置快照，鼠标一接入就自动把该电脑
对应的那套配置下发到鼠标板载存储里——插到工位就是办公参数，插到家里就是游戏参数。

## 工作原理

雷柏二代鼠标的配置（DPI、回报率、休眠、LOD……）保存在鼠标自身，主机通过 HID
**银行 / 寄存器**读写：

```
主机 --(控制接口 0x06)--> 读/写命令 --> 鼠标
主机 <--(Feature 0x08)--- 应答数据 --- 鼠标
主机 <--(状态接口 0x07)-- 电量/DPI --- 鼠标（主动广播）
```

因此"按电脑切换"不需要鼠标支持多套板载配置槽，只要：

1. 在某台电脑上把鼠标调好，`rapoo-autoswitch snapshot 办公` 存下当前寄存器；
2. `rapoo-autoswitch use 办公` 声明"这台电脑用办公"；
3. `rapoo-autoswitch daemon` 常驻，鼠标接入时自动下发这份快照。

每台电脑各自持有快照与预设（存在各自的用户配置目录），互不干扰。

## 支持设备

雷柏 **二代 Nordic 54L15 + PAW3950 系列**（VID `0x24AE`），已实测收录的型号：

| PID | 型号 |
| --- | --- |
| `1406` `1410` `1411` | 雷柏 VT3S |
| `4606` `4611` | 雷柏 VT3S（有线） |
| `1460` `4660` | 雷柏 VT7 |
| `1412` `4612` | 雷柏 VT3 |
| `1417` `4617` | 雷柏 VT3 MAX |

**VT3S V2** 若不在上表内，会走"通用"分支：按 VID + 三接口角色自动识别，功能同样可用，
只是型号名显示为设备自报的名称。欢迎实测后提 PR 补全型号表。

> 更早的雷柏机型（如 VT3 PRO）用的是另一套 EEPROM 协议（报文 ID `0xBA`），不在本项目
> 支持范围内。

## 安装

```bash
pip install -e .
```

真实读写需要 hidapi 的 Python 绑定（`pip install hid`，已列为依赖）。只跑测试或离线看
命令帮助则不需要。

想要托盘常驻版，再装可选依赖：

```bash
pip install -e ".[tray]"     # pystray + Pillow
```

## 快速开始

```bash
# 1) 确认设备已识别（会列出三个接口的角色）
rapoo-autoswitch list

# 2) 看当前状态：电量 / DPI / 连接方式
rapoo-autoswitch status

# 3) 先把鼠标调成你要的样子（含 DPI），再存成快照
rapoo-autoswitch dpi 800,1600,3200      # 设置三档 DPI
rapoo-autoswitch dpi --index 2          # 切到第 2 档
rapoo-autoswitch snapshot 办公

# 4) 声明这台电脑用哪份快照
rapoo-autoswitch use 办公

# 5) 选一种常驻方式
rapoo-autoswitch tray                   # 托盘图标：看电量、右键切配置
rapoo-autoswitch daemon                 # 无界面守护，适合开机自启

# 6) （Windows）开机自启
rapoo-autoswitch startup install
```

## 命令一览

| 命令 | 说明 |
| --- | --- |
| `list` | 列出已连接的雷柏二代鼠标及其接口路径 |
| `status` | 电量、DPI 档位与数值、连接方式、充电状态 |
| `battery` | 只打印电量，便于脚本/状态栏取用 |
| `dpi` | 查看各档 DPI；`dpi 800,1600,3200` 设置，`dpi --index 2` 切当前档位 |
| `get [寄存器...]` | 读取寄存器并解码显示 |
| `set 寄存器=值 ...` | 写入寄存器并回读校验 |
| `probe <bank> <addr> <len>` | 原始字节读取，用于逆向新机型 |
| `snapshot <名字>` | 把当前配置采集为快照 |
| `snapshots` | 列出本机所有快照，标出当前预设 |
| `apply <名字>` | 把快照下发给鼠标；`--dry-run` 只看差异 |
| `use <名字>` | 设置本机预设 |
| `current` | 显示本机配置（预设 / 间隔 / VID） |
| `tray` | 托盘常驻版：电量图标 + 右键切换配置 |
| `daemon` | 无界面常驻，接入即下发；`--once` 只跑一轮便于调试 |
| `startup install\|uninstall\|status` | Windows 开机自启管理 |

快照覆盖的寄存器（系统银行 `0x08`）：

| 名称 | 地址 | 说明 |
| --- | --- | --- |
| `dpi_table_x` | `0x88` | X 轴各档 DPI（u16le × 档位数，最多 6 档） |
| `dpi_table_y` | `0xC8` | Y 轴各档 DPI |
| `dpi_slot_count` | `0x96` | 启用的档位数（1..6） |
| `dpi_active_index` | `0x98` | 当前生效档位 |
| `polling_hz` | `0x80` | 回报率（125 / 250 / 500 / 1000 / 2000 / 4000 / 8000 Hz） |
| `key_scan_rate` | `0x81` | 按键扫描率 |
| `lod` | `0x84` | 抬升高度档位 |
| `motion_sync` | `0x85` | 移动同步 |
| `debounce_ms` | `0xC0` | 按键消抖时间 |
| `lift_delay_ms` | `0xC1` | 抬起延迟 |
| `sleep_minutes` | `0xC2` | 休眠时间（2..120 分钟） |
| `linear_ripple` | `0xC3` | bit0 直线修正、bit1 波纹控制（0 = 开启） |
| `sensor_angle` | `0xC4` | 传感器角度（有符号） |
| `glass_mode` | `0xC5` | 玻璃模式 |

下发顺序有讲究：DPI 两张表必须早于档位数，档位数早于当前档位，否则设备会按旧档位数
解释表内容。这一点由 `Register.order` 保证，已用单元测试锁住。

## 项目结构

```
src/rapoo_autoswitch/
  protocol.py    报文构造与解析、寄存器表（纯逻辑，可离线测试）
  transport.py   HID 通道：hidapi 实现 + 抽象接口
  device.py      设备发现、接口角色识别、命令会话（写后回读校验）
  profiles.py    快照采集/保存/下发/差异比较（含 DPI 变长表与下发顺序）
  config.py      用户配置目录与本机预设
  daemon.py      接入检测与自动下发
  tray.py        托盘常驻版（电量图标 + 切换配置菜单）
  autostart.py   Windows 开机自启
  cli.py         命令行入口
tests/           52 个用例，全部不需要硬件
docs/PROTOCOL.md 逆向出的协议细节与来源
```

运行测试：

```bash
pip install -e ".[dev]"
pytest
```

## 安全提示

写寄存器是**真写硬件**。对未在实测列表中确认的机型，请先在"空快照"下探索：

```bash
rapoo-autoswitch probe 0x08 0x80 1      # 先读，确认语义
rapoo-autoswitch apply 办公 --dry-run    # 只看差异，不写入
rapoo-autoswitch snapshot 原始备份       # 改之前先留一份可回滚的快照
```

快照里保存的是**原始字节**，所以即使我们某个寄存器的语义理解有误，也能原样还原。

## 参考与致谢

协议细节来自以下开源项目：

- [Iris-0109/rapoo-tray](https://github.com/Iris-0109/rapoo-tray)（MIT）—— 三接口模型、
  状态广播报文布局、系统寄存器地址、已实测的机型 PID 表（**代码移植**）
- [D3m0nZOnFire/mousectl](https://github.com/D3m0nZOnFire/mousectl)（MIT）—— 银行/寄存器
  读写模型、"写后回读校验"策略（**代码移植**）
- [Nuitfanee/ClickSync](https://github.com/Nuitfanee/ClickSync)（GPL-2.0）—— DPI 表地址、
  档位数/当前档位的编码与下发顺序等**协议事实**（仅读取源码获取常量，**未复制任何代码**）

本项目为 MIT 许可。GPL-2.0 的 ClickSync 与 MIT 不兼容，因此只从中提取协议事实（寄存器
地址、编码规则这类互操作性所必需的功能性信息），实现全部自行编写。详见
[docs/PROTOCOL.md](docs/PROTOCOL.md)。

## License

MIT
