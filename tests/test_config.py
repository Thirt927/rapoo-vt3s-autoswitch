"""本机配置：默认值、预设设置与落盘。"""

from rapoo_autoswitch import config


def test_default_config_has_expected_keys():
    cfg = config.load_config()
    assert cfg["preset"] is None
    assert cfg["poll_interval_s"] == 3.0
    assert cfg["vendor_id"] == 0x24AE


def test_set_preset_persists():
    config.set_preset("办公")
    assert config.load_config()["preset"] == "办公"

    # 再写一次不应丢掉其它字段
    config.set_preset("游戏")
    cfg = config.load_config()
    assert cfg["preset"] == "游戏"
    assert cfg["poll_interval_s"] == 3.0


def test_config_and_snapshot_dirs_exist():
    assert config.config_dir().is_dir()
    assert config.snapshots_dir().is_dir()
    assert config.snapshots_dir().parent == config.config_dir()


def test_machine_name_is_non_empty():
    assert config.machine_name()
