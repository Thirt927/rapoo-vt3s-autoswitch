"""让 tests/ 能直接 import src/ 下的包，无需先 pip install。"""

import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """所有测试的配置/快照都写到临时目录，不碰真实用户配置。"""
    monkeypatch.setenv("RAPOO_AUTOSWITCH_HOME", str(tmp_path / "home"))
