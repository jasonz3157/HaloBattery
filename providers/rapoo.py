"""雷柏 VT7 (Gen-2) 的被动电量读取.

协议来源: RAPOO HUB 的 VT Generation 2 Series 状态报告,
https://hub.rapoo.com/, 以及上游 PR #215 的协议说明:
https://github.com/HeyOkay/HaloBattery/pull/215.

Windows hidapi 返回的 input report 包含 report ID: 索引 0 为 0x07,
索引 7 为电池状态 (1 为电池供电, 2 为充电), 索引 8 为百分比.
2026-10-26 在 jasonz3157 的 VT7 接收器 24AE:1413 上实测:
    07 10 04 08 07 08 07 02 1b 00 03 01 00 13 46 01 02 02 c8
对应 27% 和充电状态, 来自 usage FF00:0002. 充电时约 3 秒上报一次,
电池供电时上报间隔可超过 5 秒, 因此在轮询之间持续监听.
USB 连接 ID 24AE:4613 来自上游的实机记录, 本地尚未单独验证.

仅持续监听实测的厂商 collection FF00:0002, 不发送命令.
接收器和 USB 连接共用一个图标, 有充电状态的有效读数优先.
"""

from __future__ import annotations

import threading
import time
from collections import deque

try:
    import hid
except ImportError:  # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

RAPOO_VID = 0x24AE
RECEIVER_PID = 0x1413
CABLE_PID = 0x4613
PIDS = (RECEIVER_PID, CABLE_PID)
NAME = "Rapoo VT7 (Gen-2)"
SHARED_KEY = "rapoo:1413"

VENDOR_PAGE = 0xFF00
VENDOR_USAGE = 0x0002
REPORT_ID = 0x07
STATUS_INDEX = 7
LEVEL_INDEX = 8
ON_BATTERY = 1
CHARGING = 2

READ_TIMEOUT_MS = 250
PROBE_LISTEN_S = 10.0
ONLINE_FOR = 30  # 短暂没有上报不等于休眠, 允许多个状态周期.
ASLEEP_KEEP = 300

Reading = tuple[int, bool]


def parse_report(report) -> Reading | None:
    """提取有效状态报告的百分比和充电标记."""
    if not report or len(report) <= LEVEL_INDEX or report[0] != REPORT_ID:
        return None
    state, level = report[STATUS_INDEX], report[LEVEL_INDEX]
    if state not in (ON_BATTERY, CHARGING) or not 0 <= level <= 100:
        return None
    return level, state == CHARGING


def candidates(ifaces: list[dict]) -> list[dict]:
    """只选择实测状态接口, 按 path 去重."""
    seen = set()
    out = []
    for item in ifaces:
        if (
            item.get("usage_page") != VENDOR_PAGE
            or item.get("usage") != VENDOR_USAGE
            or item["path"] in seen
        ):
            continue
        seen.add(item["path"])
        out.append(item)
    return out


class _Reader:
    """持续接收设备上报, 使 poll 不阻塞也不会错过两次轮询间的报文."""

    def __init__(self, path: bytes):
        self.path = path
        self._lock = threading.Lock()
        self._last: tuple[int, bool, float] | None = None
        self._lines: deque[str] = deque(maxlen=32)
        self._stop = threading.Event()
        self.heard = threading.Event()
        self.thread = threading.Thread(target=self._run, name="rapoo-vt7", daemon=True)
        self.thread.start()

    def alive(self) -> bool:
        return self.thread.is_alive()

    def stop(self) -> None:
        self._stop.set()

    def take(self) -> tuple[tuple[int, bool, float] | None, list[str]]:
        with self._lock:
            result = self._last, list(self._lines)
            self._lines.clear()
            return result

    def _note(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)

    def _run(self) -> None:
        device = None
        try:
            device = hid.device()
            device.open_path(self.path)
            while not self._stop.is_set():
                report = device.read(64, READ_TIMEOUT_MS)
                if not report:
                    continue
                reading = parse_report(report)
                with self._lock:
                    self._lines.append(f"    report: {hexdump(report)}")
                    if reading is not None:
                        self._last = (*reading, time.monotonic())
                        self.heard.set()
        except (OSError, ValueError) as error:
            self._note(f"    open/read error: {error}")
        finally:
            if device is not None:
                try:
                    device.close()
                except (OSError, ValueError) as error:
                    self._note(f"    close error: {error}")
            self.heard.set()  # 打开失败时也唤醒一次性 probe.


class RapooProvider(Provider):
    name = "rapoo"

    def __init__(self, listen_first: float = 0.0):
        self._lock = threading.RLock()
        self._diag: list[str] = []
        self._readers: dict[bytes, _Reader] = {}
        self._last: tuple[int, bool, float] | None = None
        self._listen_first = listen_first

    def close(self) -> None:
        """释放后台监听器, 供 probe 和独立调用者结束读取."""
        with self._lock:
            self._close()

    def _close(self) -> None:
        readers, self._readers = list(self._readers.values()), {}
        for reader in readers:
            reader.stop()
        for reader in readers:
            reader.thread.join(timeout=1.0)
        self._last = None

    def poll(self) -> list[DeviceStatus]:
        with self._lock:
            return self._poll()

    def _poll(self) -> list[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(RAPOO_VID)
        except (OSError, ValueError) as error:
            log.warning("hid.enumerate(rapoo): %s", error)
            return []
        mine = [item for item in infos if item.get("product_id") in PIDS]
        if not mine:
            self.close()
            self._last = None
            return []

        selected = []
        for pid in PIDS:
            order = candidates([item for item in mine if item["product_id"] == pid])
            if order:
                selected.append(order[0])
        present = {item["path"] for item in selected}
        for path in set(self._readers) - present:
            self._readers.pop(path).stop()

        new_readers = []
        for item in selected:
            path = item["path"]
            reader = self._readers.get(path)
            if reader is None or not reader.alive():
                if reader is not None:
                    _, lines = reader.take()
                    self._diag += lines
                reader = self._readers[path] = _Reader(path)
                new_readers.append(reader)
            self._diag.append(
                f"[Rapoo] pid={item['product_id']:04x} '{NAME}' "
                f"iface={item.get('interface_number')} "
                f"usage={VENDOR_PAGE:04x}:{VENDOR_USAGE:04x}"
            )
        deadline = time.monotonic() + self._listen_first
        for reader in new_readers:
            if self._listen_first:
                reader.heard.wait(max(0.0, deadline - time.monotonic()))

        readings = []
        for reader in self._readers.values():
            reading, lines = reader.take()
            self._diag += lines
            if reading is not None:
                readings.append((*reading, reader.alive()))
        now = time.monotonic()
        fresh = [item for item in readings if item[3] and now - item[2] < ONLINE_FOR]
        if fresh:
            level, charging, heard_at, _ = max(
                fresh, key=lambda item: (item[1], item[2])
            )
            self._last = level, charging, heard_at
            return [
                DeviceStatus(
                    SHARED_KEY, NAME, level, charging, True, "rapoo", kind="mouse"
                )
            ]
        if readings:
            newest = max(readings, key=lambda item: item[2])
            if self._last is None or newest[2] > self._last[2]:
                self._last = newest[:3]
        if self._last and now - self._last[2] < ASLEEP_KEEP:
            self._diag.append("  nothing new; keeping the last level, greyed out")
            return [
                DeviceStatus(
                    SHARED_KEY,
                    NAME,
                    self._last[0],
                    self._last[1],
                    False,
                    "rapoo",
                    kind="mouse",
                    wake_on_input=True,
                )
            ]
        self._last = None
        return []

    def diagnostics(self) -> list[str]:
        return list(self._diag)
