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

## 快速开始

```bash
# 1) 确认设备已识别（会列出三个接口的角色）
rapoo-autoswitch list

# 2) 看当前状态：电量 / DPI / 连接方式
rapoo-autoswitch status

# 3) 先把鼠标调成你要的样子，再存成快照
rapoo-autoswitch snapshot 办公

# 4) 声明这台电脑用哪份快照
rapoo-autoswitch use 办公

# 5) 常驻，接入即自动下发
rapoo-autoswitch daemon

# 6) （Windows）开机自启
rapoo-autoswitch startup install
```

## 命令一览

| 命令 | 说明 |
| --- | --- |
| `list` | 列出已连接的雷柏二代鼠标及其接口路径 |
| `status` | 电量、DPI 档位与数值、连接方式、充电状态 |
| `battery` | 只打印电量，便于脚本/状态栏取用 |
| `get [寄存器...]` | 读取寄存器并解码显示 |
| `set 寄存器=值 ...` | 写入寄存器并回读校验 |
| `probe <bank> <addr> <len>` | 原始字节读取，用于逆向新机型 |
| `snapshot <名字>` | 把当前配置采集为快照 |
| `snapshots` | 列出本机所有快照，标出当前预设 |
| `apply <名字>` | 把快照下发给鼠标；`--dry-run` 只看差异 |
| `use <名字>` | 设置本机预设 |
| `current` | 显示本机配置（预设 / 间隔 / VID） |
| `daemon` | 常驻，接入即下发；`--once` 只跑一轮便于调试 |
| `startup install\|uninstall\|status` | Windows 开机自启管理 |

已知寄存器（系统银行 `0x08`）：

| 名称 | 地址 | 说明 |
| --- | --- | --- |
| `polling_hz` | `0x80` | 回报率（125 / 250 / 500 / 1000 / 2000 / 4000 / 8000 Hz） |
| `key_scan_rate` | `0x81` | 按键扫描率 |
| `lod` | `0x84` | 抬升高度档位 |
| `motion_sync` | `0x85` | 移动同步 |
| `sleep_minutes` | `0xC2` | 休眠时间（2..120 分钟） |
| `linear_ripple` | `0xC3` | 直线修正 / 波纹控制标志位 |
| `sensor_angle` | `0xC4` | 传感器角度（有符号） |
| `glass_mode` | `0xC5` | 玻璃模式 |

## 项目结构

```
src/rapoo_autoswitch/
  protocol.py    报文构造与解析、寄存器表（纯逻辑，可离线测试）
  transport.py   HID 通道：hidapi 实现 + 抽象接口
  device.py      设备发现、接口角色识别、命令会话（写后回读校验）
  profiles.py    快照采集/保存/下发/差异比较
  config.py      用户配置目录与本机预设
  daemon.py      接入检测与自动下发
  autostart.py   Windows 开机自启
  cli.py         命令行入口
tests/           38 个用例，全部不需要硬件
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

协议细节来自以下开源项目（本项目只移植了 **MIT** 许可项目的实现思路，GPL 项目仅用于
交叉验证，未复制代码）：

- [Iris-0109/rapoo-tray](https://github.com/Iris-0109/rapoo-tray)（MIT）—— 三接口模型、
  状态广播报文布局、系统寄存器地址、已实测的机型 PID 表
- [D3m0nZOnFire/mousectl](https://github.com/D3m0nZOnFire/mousectl)（MIT）—— 银行/寄存器
  读写模型、"写后回读校验"策略
- [Nuitfanee/ClickSync](https://github.com/Nuitfanee/ClickSync)（GPL-2.0）—— 多品牌 WebHID
  驱动，仅作交叉验证参考

详见 [docs/PROTOCOL.md](docs/PROTOCOL.md)。

## License

MIT
