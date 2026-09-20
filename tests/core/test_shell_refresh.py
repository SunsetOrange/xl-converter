import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PySide6.QtCore import QObject, Signal

import core.shell_refresh as shell_refresh

class FakeItems:
    def __init__(self, items):
        self.items = items

    def getItem(self, n):
        return self.items[n]

    def getItemCount(self):
        return len(self.items)

class FakeController(QObject):
    processing_started = Signal()
    processing_finished = Signal()

    def __init__(self, items):
        super().__init__()
        self.items = FakeItems(items)

def n(path):
    return os.path.normcase(os.path.abspath(path))

OUTPUT_SETTINGS = {
    "custom_output_dir": False,
    "custom_output_dir_path": "",
    "keep_dir_struct": False,
}

@pytest.fixture
def notify():
    with patch.object(shell_refresh, "IS_WINDOWS", True), \
        patch.object(shell_refresh, "_SHChangeNotify", create=True) as mock:
        yield mock

@pytest.fixture
def refresher(app, notify):
    controller = FakeController([(Path("/src/a/img.jpg"), Path("/src"))])
    threadpool = MagicMock()
    threadpool.activeThreadCount.return_value = 0
    r = shell_refresh.ShellRefresher(controller, threadpool, lambda: OUTPUT_SETTINGS)
    yield r, controller, threadpool, notify
    r.timer.stop()

# refreshDirs

def test_refreshDirs_calls_per_dir(notify):
    shell_refresh.refreshDirs(["/a", "/b"])
    assert notify.call_count == 2
    for c in notify.call_args_list:
        assert c.args[0] == shell_refresh.SHCNE_UPDATEDIR
        assert c.args[1] == shell_refresh.SHCNF_PATHW
    assert [c.args[2].value for c in notify.call_args_list] == ["/a", "/b"]

def test_refreshDirs_swallows_exceptions(notify):
    notify.side_effect = OSError
    shell_refresh.refreshDirs(["/a", "/b"])
    assert notify.call_count == 2

def test_refreshDirs_noop_on_non_windows():
    with patch.object(shell_refresh, "IS_WINDOWS", False), \
        patch.object(shell_refresh, "_SHChangeNotify", create=True) as notify:
        shell_refresh.refreshDirs(["/a"])
        notify.assert_not_called()

# collectDirs

def test_collectDirs_same_dir():
    dirs = shell_refresh.collectDirs([(Path("/src/a/img.jpg"), Path("/src"))], OUTPUT_SETTINGS)
    assert dirs == {n("/src/a"), n("/src")}

def test_collectDirs_custom_absolute():
    settings = OUTPUT_SETTINGS | {"custom_output_dir": True, "custom_output_dir_path": os.path.abspath("/out/x")}
    dirs = shell_refresh.collectDirs([(Path("/src/a/img.jpg"), Path("/src"))], settings)
    assert dirs == {n("/src/a"), n("/out/x"), n("/out")}

def test_collectDirs_custom_relative():
    settings = OUTPUT_SETTINGS | {"custom_output_dir": True, "custom_output_dir_path": "converted"}
    dirs = shell_refresh.collectDirs([(Path("/src/a/img.jpg"), Path("/src"))], settings)
    assert dirs == {n("/src/a"), n("/src/a/converted")}

def test_collectDirs_keep_dir_struct():
    settings = OUTPUT_SETTINGS | {"custom_output_dir": True, "custom_output_dir_path": os.path.abspath("/out"), "keep_dir_struct": True}
    dirs = shell_refresh.collectDirs([(Path("/src/a/b/img.jpg"), Path("/src"))], settings)
    assert dirs == {n("/src/a/b"), n("/out/a/b"), n("/out/a")}

def test_collectDirs_deduplicates():
    items = [
        (Path("/src/a/1.jpg"), Path("/src")),
        (Path("/src/a/2.jpg"), Path("/src")),
        (Path("/SRC/A/3.jpg"), Path("/src")),
    ]
    dirs = shell_refresh.collectDirs(items, OUTPUT_SETTINGS)
    expected = {n("/src/a"), n("/src"), n("/SRC/A"), n("/SRC")}
    assert dirs == expected     # normcase collapses case on Windows only

# ShellRefresher

def test_refresh_waits_for_threads(refresher):
    r, controller, threadpool, notify = refresher
    threadpool.activeThreadCount.return_value = 2
    controller.processing_started.emit()
    controller.processing_finished.emit()
    notify.assert_not_called()
    assert r.timer.isActive()

    threadpool.activeThreadCount.return_value = 0
    r._poll()
    assert notify.call_count == 2
    assert not r.timer.isActive()

def test_refresh_immediate_when_idle(refresher):
    r, controller, threadpool, notify = refresher
    controller.processing_started.emit()
    controller.processing_finished.emit()
    assert notify.call_count == 2
    assert not r.timer.isActive()

def test_refresh_once_per_task(refresher):
    r, controller, threadpool, notify = refresher
    controller.processing_started.emit()
    controller.processing_finished.emit()
    controller.processing_finished.emit()
    r._poll()
    assert notify.call_count == 2

def test_pending_refresh_flushed_on_new_task(refresher):
    r, controller, threadpool, notify = refresher
    threadpool.activeThreadCount.return_value = 1
    controller.processing_started.emit()
    controller.processing_finished.emit()
    notify.assert_not_called()

    controller.processing_started.emit()
    assert notify.call_count == 2
    assert not r.pending
    assert r.dirs == {n("/src/a"), n("/src")}

def test_not_connected_on_non_windows(app):
    with patch.object(shell_refresh, "IS_WINDOWS", False), \
        patch.object(shell_refresh, "refreshDirs") as refresh:
        controller = FakeController([(Path("/src/a/img.jpg"), Path("/src"))])
        threadpool = MagicMock()
        threadpool.activeThreadCount.return_value = 0
        r = shell_refresh.ShellRefresher(controller, threadpool, lambda: OUTPUT_SETTINGS)
        controller.processing_started.emit()
        controller.processing_finished.emit()
        refresh.assert_not_called()
        assert r.dirs == set()
