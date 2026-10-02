"""托盘版：依赖缺失时的提示，以及 CLI 接线。"""

import pytest

from rapoo_autoswitch import cli, tray


def _has_tray_deps() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(_has_tray_deps(), reason="环境已安装 pystray/Pillow")
def test_missing_tray_dependency_gives_actionable_error():
    with pytest.raises(RuntimeError, match="rapoo-autoswitch\\[tray\\]"):
        tray.TrayApp()


def test_cli_exposes_tray_command():
    parser = cli.build_parser()
    args = parser.parse_args(["tray", "--preset", "办公", "--interval", "2"])
    assert args.preset == "办公"
    assert args.interval == 2.0
    assert args.command == "tray"
