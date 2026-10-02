# rapoo-vt3s-autoswitch

按**电脑**自动切换雷柏二代鼠标配置的命令行工具 + 后台守护。

官方 A-Hub 里能存「出厂配置 / 办公 / 游戏」等好几套配置，但它不会根据你把鼠标插在
**哪台电脑**上自动切换。本项目在每台电脑上各存一份配置快照，鼠标一接入就自动把该电脑
对应的那套配置下发到鼠标板载存储里——插到工位就是办公参数，插到家里就是游戏参数。

## 怎么开始

两条路，挑一条：

| 你 | 走法 |
| --- | --- |
| **不想碰命令行**（推荐） | 下载 `rapoo-autoswitch.exe` → 放进一个纯英文目录 → **双击** → 跟着向导走。**不用装 Python**。 |
| 会用命令行 | 双击 **`安装.bat`**：自动找 Python、检查路径、装依赖、自检，再问你要不要跑向导。 |

> **⚠️ 路径别带中文。** 中文路径会破坏 Python 的安装（生成的 `.pth` 是 GBK 编码，
> Python 读不回来），症状是莫名其妙的 `ModuleNotFoundError` 或
> `Fatal Python error: init_import_site`。放到 `D:\rapoo` 这类纯英文路径最省心——
> `安装.bat` 和 `doctor` 都会替你检查这一条。

装完之后，日常就两个命令：

```bash
rapoo-autoswitch doctor    # 出任何问题，先跑它：环境自检 + 修复建议
rapoo-autoswitch setup     # 向导：采集配置 → 设为本机预设 → 装开机自启
```

## 工作原理

雷柏二代鼠标的配置（DPI、回报率、休眠、LOD……）保存在鼠标自身，主机通过 HID
**银行 / 寄存器**读写：

```
主机 --(控制接口 0x06)--> 读/写命令 --> 鼠标
主机 <--(Feature 0x08)--- 应答数据 --- 鼠标
主机 <--(状态接口 0x07)-- 电量/DPI --- 鼠标（主动广播）
```

雷柏二代鼠标的板载 Flash 里存着**多份完整配置**（每 4 个 bank 一组，如 `0x06/0x08`、
`0x0A/0x0C`……），但**只有当前选中那一组会真正生效**；选中哪一组由鼠标里的指针
`bank 0x01 : 0x0C` 决定。详见 [docs/PROTOCOL.md](docs/PROTOCOL.md)。

因此"按电脑切换"只要：

1. 在某台电脑上把鼠标调好，`rapoo-autoswitch snapshot 办公` 存下**当前生效那份**配置；
2. `rapoo-autoswitch use 办公` 声明"这台电脑用办公"；
3. `rapoo-autoswitch daemon` 常驻，鼠标接入时自动下发这份快照——它会写到鼠标
   **当前生效的那一组**，所以立刻生效，全程不需要打开官方 A-Hub。

每台电脑各自持有快照与预设（存在各自的用户配置目录），互不干扰。

## 支持设备

雷柏 **二代 Nordic 54L15 + PAW3950 系列**（VID `0x24AE`）：

| PID | 型号 |
| --- | --- |
| `1406` `1410` `1411` | 雷柏 VT3S |
| `4606` `4611` | 雷柏 VT3S（有线） |
| `1464` | 雷柏 VT3S V2 |
| `4664` | 雷柏 VT3S V2（有线） |
| `1460` `4660` | 雷柏 VT7 |
| `1412` `4612` | 雷柏 VT3 |
| `1417` `4617` | 雷柏 VT3 MAX |

`1406/1410/1411/4606/4611/1460/4660/1412/4612/1417/4617` 来自 rapoo-tray 的实测记录；
`1464`（VT3S V2）为本项目在真机上确认，其有线变体 `4664` 按"有线机型 PID 以 `46` 开头"
的规律推得，**尚未实测**。

**未收录的机型**会走"通用"分支：按 VID + 三接口角色自动识别，功能同样可用，只是型号名
显示为设备自报的名称——欢迎实测后提 PR 补全型号表。

> 更早的雷柏机型（如 VT3 PRO）用的是另一套 EEPROM 协议（报文 ID `0xBA`），不在本项目
> 支持范围内。

## 安装

### 方式一：免安装 exe（推荐，不用装 Python）

1. 到 [Releases](../../releases) 下载 `rapoo-autoswitch.exe`；
2. 放进一个**纯英文目录**，例如 `D:\rapoo`（放哪都行，别带中文）；
3. **双击它**——会弹出一个中文菜单（有 `1) 配置向导`、`2) 环境自检`、`4) 托盘常驻` 等
   选项）。第一次用就选 `1` 跟着向导走。也可以直接在终端里敲子命令：

```bash
rapoo-autoswitch.exe setup
```

> 菜单里的「托盘常驻」是**后台启动**：选完它这个窗口就可以关掉了，图标会留在右下角。

exe 是**单文件、自带 Python 运行时**（约 28 MB），不需要装任何东西。每台电脑各放一份。

### 方式二：一键脚本（已装 Python）

1. 装 Python 3.9+，安装时**务必勾选 `Add python.exe to PATH`**（漏了会提示找不到 pip）；
2. 下载本项目 ZIP，解压到**纯英文路径**（如 `D:\rapoo`）；
3. **双击 `安装.bat`**，它会依次：找 Python → 检查路径 → 装依赖 → 自检 → 问你要不要跑向导。

> 全程不需要管理员权限。想自己敲命令的话见下面的「方式三」。

### 方式三：手动安装

```bash
pip install -e ".[tray]"   # -e 可编辑模式；[tray] 带上托盘依赖（不加也能用 daemon）
rapoo-autoswitch doctor    # 自检：路径 / HID 后端 / 设备 / A-Hub 占用 / 开机自启
```

### ⚠️ 最常见的坑 1：路径带中文

见上方「怎么开始」里的警告。一句话：**放纯英文路径**。`安装.bat` 会在安装前拦下你，
`doctor` 也会报出来。

### ⚠️ 最常见的坑 2：`hid` 和 `hidapi` 是两个不同的包

PyPI 上**两个包都提供 `import hid`**，但只有一个能用：

| 包 | 说明 |
| --- | --- |
| **`hidapi`**（trezor/cython-hidapi） | 编译扩展，原生库**静态链接**，装完即用 ✅ 本项目依赖它 |
| `hid`（apmorton/pyhidapi） | 只是 ctypes 绑定，**不含原生库**，Windows 上会因找不到 `hidapi.dll` 而在导入时失败 ❌ |

如果你之前装过 `hid`，会看到"未安装 hidapi 绑定"这类报错，即使 pip 说依赖已满足。解决：

```bash
pip uninstall -y hid
pip install hidapi
```

### 常见报错

**先跑 `rapoo-autoswitch doctor`**，多数问题它会直接指出并给修复命令。

| 报错 | 原因与解法 |
| --- | --- |
| `'pip' 不是内部或外部命令` | Python 没加进 PATH，改用 `py -m pip install -e .` |
| `'rapoo-autoswitch' 不是内部或外部命令` | 装好了但 Scripts 目录不在 PATH，改用 `py -m rapoo_autoswitch list` |
| `Fatal Python error: init_import_site` / `ModuleNotFoundError: rapoo_autoswitch` | **路径带中文**，`pip` 生成的 `.pth` 编码不对。移到纯英文路径重装 |
| `error in 'egg_base' option: 'src' does not exist` | 项目文件不完整（多为下载/解压不全），重新完整下载 |
| `未找到可用的 HID 通信库` | 见上方 `hid` / `hidapi` 一节 |
| `没有收到状态广播` | 状态报文是"谁先读谁拿到"：先退出官方 A-Hub 与本工具的 `tray`；鼠标休眠（默认 10 分钟）时会停推，晃一下唤醒 |
| 下发提示成功、鼠标却没变 | 你改的不是"当前生效"那一组配置。本项目会**自动写到生效组**，请确认用的是含该修复的版本 |

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

# 6) （Windows）开机自启：默认 daemon（无界面）；想要托盘电量图标加 --mode tray
rapoo-autoswitch startup install
```

### 托盘图标关掉了，怎么再打开？

不小心把右下角的小图标关掉（或右键选了「退出」）之后，重新拉起来：

| 你 | 操作 |
| --- | --- |
| **exe 用户**（推荐） | 双击 `rapoo-autoswitch.exe` → 菜单里选 `4) 托盘常驻` |
| 命令行 | `rapoo-autoswitch.exe tray` |

托盘是**后台程序**，启动后会自动从终端脱离，所以**关掉那个黑框 / 终端窗口不会影响它**，
图标会照常留着。想看着日志跑、要 Ctrl+C 才停，用无界面的 `rapoo-autoswitch daemon`。

> 用 exe 时任务管理器里会看到**两个** `rapoo-autoswitch.exe`——单文件打包的引导进程 +
> 真正干活的进程，属正常现象，不影响使用。

不想每次手动开，就让它开机自启（这样插上电就已经在跑了）：

```bash
rapoo-autoswitch.exe startup install --mode tray    # 开机自启托盘版
rapoo-autoswitch.exe startup status                 # 看当前自启方式
rapoo-autoswitch.exe startup uninstall              # 关掉自启
```

托盘脱离终端后的输出（含出错信息）写在 `%APPDATA%\rapoo-autoswitch\tray.log`。

## 命令一览

| 命令 | 说明 |
| --- | --- |
| `doctor` | **环境自检**：Python / 安装路径编码 / HID 后端 / 托盘依赖 / 设备 / A-Hub 占用 / 开机自启 / 预设，逐项给修复建议 |
| `setup` | **上手向导**：采集当前配置 → 设为本机预设 → 选常驻方式并装开机自启 |
| `list` | 列出已连接的雷柏二代鼠标及其接口路径 |
| `status` | 电量、DPI 档位与数值、回报率、性能模式（扫描率档位）、连接方式、充电状态 |
| `watch` | 打印状态接口的原始字节，排查"收不到电量广播" |
| `battery` | 只打印电量，便于脚本/状态栏取用 |
| `dpi` | 查看各档 DPI；`dpi 800,1600,3200` 设置，`dpi --index 2` 切当前档位 |
| `get [寄存器...]` | 读取寄存器并解码显示 |
| `set 寄存器=值 ...` | 写入寄存器并回读校验 |
| `dump [bank] [start] [end]` | 整段读出寄存器逐行打印，用于对比不同配置的差异 |
| `probe <bank> <addr> <len>` | 原始字节读取，用于逆向新机型 |
| `snapshot <名字>` | 把当前配置采集为快照 |
| `snapshots` | 列出本机所有快照，标出当前预设 |
| `apply <名字>` | 把快照下发给鼠标；`--dry-run` 只看差异 |
| `use <名字>` | 设置本机预设 |
| `current` | 显示本机配置（预设 / 间隔 / VID） |
| `tray` | 托盘常驻版：电量图标 + 右键切换配置；启动后脱离终端，关掉窗口也不影响它 |
| `daemon` | 无界面常驻，接入即下发；`--once` 只跑一轮便于调试 |
| `startup install\|uninstall\|status` | Windows 开机自启管理；`install --mode tray` 改为自启托盘版（默认 `daemon` 无界面） |

快照覆盖的寄存器（写入"当前生效那一组"的系统银行，逻辑地址仍记作 `0x08`）：

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
| `performance_mode` | `0xDC` | 性能模式块（A-Hub 的「性能模式/扫描率」），7 字节原始值，**随回报率联动**（详见 [docs/PROTOCOL.md](docs/PROTOCOL.md)）；`status` 只在实测确认过的组合上给出档位名，其余原样显示字节 |

下发顺序有讲究：DPI 两张表必须早于档位数，档位数早于当前档位，否则设备会按旧档位数
解释表内容。这一点由 `Register.order` 保证，已用单元测试锁住。

## 项目结构

```
安装.bat                         Windows 一键安装（找 Python / 查路径 / 装依赖 / 自检 / 向导）
src/rapoo_autoswitch/
  protocol.py    报文构造与解析、寄存器表（纯逻辑，可离线测试）
  transport.py   HID 通道：hidapi 实现 + 抽象接口
  device.py      设备发现、接口角色识别、命令会话（写后回读校验）
  profiles.py    快照采集/保存/下发/差异比较（含 DPI 变长表与下发顺序）
  config.py      用户配置目录与本机预设
  daemon.py      接入检测与自动下发
  tray.py        托盘常驻版（数字电量图标 + 切换配置菜单）
  autostart.py   Windows 开机自启（daemon / tray 二选一）
  doctor.py      环境自检
  cli.py         命令行入口（含 setup 向导）
packaging/       PyInstaller 打包配置（产出免安装 exe）
tests/           64 个用例，全部不需要硬件
docs/PROTOCOL.md 逆向出的协议细节与来源
```

运行测试：

```bash
pip install -e ".[dev]"
pytest
```

## 给维护者：打包免安装 exe

```bash
pip install pyinstaller
pyinstaller --noconfirm packaging/rapoo-autoswitch.spec
# 产出 dist/rapoo-autoswitch.exe（单文件，约 28 MB）
```

打包后建议跑一遍 `dist\rapoo-autoswitch.exe doctor` 确认依赖都进去了
（`pystray` / `Pillow` / `hid` 都是运行时才导入的，已在 spec 的 `hiddenimports` 里声明）。

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
