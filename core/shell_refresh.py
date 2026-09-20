"""Instructs Windows Explorer to refresh any open folder views for directories touched by a conversion.

Explorer and the desktop can continue showing stale contents after files are created, renamed or deleted, especially on
network folders. After a conversion task has completely finished, a SHCNE_UPDATEDIR message is sent to Windows once for
every input and output folder of that task. This triggers a refresh in any Explorer windows open at those directories.
"""
import ctypes
import logging
import os
import platform
from collections.abc import Callable, Iterable
from ctypes import wintypes
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThreadPool, QTimer, Slot

from core.controller import Controller
from core.pathing import getOutputDir
from data.items import Items

IS_WINDOWS = platform.system() == "Windows"

SHCNE_UPDATEDIR  = 0x00001000
SHCNF_PATHW      = 0x0005
POLL_INTERVAL_MS = 200

if IS_WINDOWS:
    _SHChangeNotify = ctypes.WinDLL("shell32").SHChangeNotify
    _SHChangeNotify.argtypes = (wintypes.LONG, wintypes.UINT, wintypes.LPCVOID, wintypes.LPCVOID)
    _SHChangeNotify.restype = None


class ShellRefresher(QObject):
    """Sends one set of refresh notifications per conversion task, after all workers have exited."""

    def __init__(
        self,
        controller: Controller,
        threadpool: QThreadPool,
        get_output_settings: Callable[[], dict[str, Any]],
    ) -> None:
        super().__init__()
        self.controller: Controller = controller
        self.threadpool: QThreadPool = threadpool
        self.get_output_settings: Callable[[], dict[str, Any]] = get_output_settings

        self.dirs: set[str] = set()
        self.pending: bool = False

        self.timer: QTimer = QTimer(self)
        self.timer.setInterval(POLL_INTERVAL_MS)
        self.timer.timeout.connect(self._poll)

        if IS_WINDOWS:
            self.controller.processing_started.connect(self._onStarted)
            self.controller.processing_finished.connect(self._onFinished)

    @Slot()
    def _onStarted(self) -> None:
        if self.pending:  # A new run can only start once all threads are idle, so the previous task is done.
            self._refresh()

        try:
            items: Items = self.controller.items
            item_list: list[tuple[Path, Path]] = [items.getItem(i) for i in range(items.getItemCount())]
            self.dirs = collectDirs(item_list, self.get_output_settings())
        except Exception as e:
            self.dirs = set()
            logging.error(f"[ShellRefresh] Failed to collect dirs. {e}")

    @Slot()
    def _onFinished(self) -> None:
        if self.pending:
            return
        self.pending = True
        self._poll()
        if self.pending:
            self.timer.start()

    @Slot()
    def _poll(self) -> None:
        if self.threadpool.activeThreadCount() == 0:
            self._refresh()

    def _refresh(self) -> None:
        self.timer.stop()
        self.pending = False
        dirs, self.dirs = self.dirs, set()
        refreshDirs(sorted(dirs))


def refreshDirs(dirs: Iterable[str]) -> None:
    """Notifies the shell that the contents of each dir changed. Never raises."""
    if not IS_WINDOWS:
        return

    for d in dirs:
        try:
            _SHChangeNotify(SHCNE_UPDATEDIR, SHCNF_PATHW, ctypes.c_wchar_p(d), None)
            logging.debug(f"[ShellRefresh] Refreshing {d}")
        except Exception as e:
            logging.error(f"[ShellRefresh] Failed to refresh {d}. {e}")


def _normalize(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def collectDirs(items: list[tuple[Path, Path]], output_settings: dict[str, Any]) -> set[str]:
    """Returns every input dir, output dir, and output dir parent for the given (abs_path, anchor_path) items."""
    dirs: set[str] = set()
    for abs_path, anchor_path in items:
        item_dir = str(Path(abs_path).parent)
        dirs.add(_normalize(item_dir))
        try:
            output_dir: str = getOutputDir(
                item_dir,
                anchor_path,
                output_settings["custom_output_dir"],
                output_settings["custom_output_dir_path"],
                output_settings["keep_dir_struct"],
            )
            dirs.add(_normalize(output_dir))
            dirs.add(_normalize(os.path.dirname(os.path.abspath(output_dir))))  # Shows newly created subfolders.
        except Exception as e:
            logging.error(f"[ShellRefresh] Failed to resolve output dir for {abs_path}. {e}")
    return dirs
