"""通过公开 poll 接口和异步 HID 边界回放 VT7 实机报文."""

import os
import queue
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import rapoo as R

# 2026-10-26 从用户的 24AE:1413, FF00:0002 接口原样读取.
CAPTURE_27 = bytes.fromhex("07 10 04 08 07 08 07 02 1b 00 03 01 00 13 46 01 02 02 c8")
CAPTURE_36 = bytes.fromhex("07 10 04 08 07 08 07 02 24 00 03 01 00 13 46 01 02 02 c0")
CAPTURE_42 = bytes.fromhex("07 10 04 08 07 08 07 01 2a 00 03 01 00 13 46 01 02 02 c6")


def status(level, state=1):
    """按厂商协议构造边界状态, 不替代上面的实机报文."""
    return bytes([7, 0, 0, 0, 0, 0, 0, state, level])


def entry(path=b"receiver", pid=0x1413, page=0xFF00, usage=2):
    return {
        "path": path,
        "product_id": pid,
        "usage_page": page,
        "usage": usage,
        "interface_number": 1,
    }


class FakeBus:
    """使用队列模拟设备主动上报, 不提供写入或 feature report 方法."""

    def __init__(self):
        self.queues = {b"receiver": queue.Queue(), b"cable": queue.Queue()}
        self.open_errors = set()
        self.opened = []
        self.closed = []

    def push(self, report, path=b"receiver"):
        self.queues[path].put(report)

    def device(self):
        bus = self

        class Device:
            def open_path(self, path):
                self.path = path
                bus.opened.append(path)
                if path in bus.open_errors:
                    raise OSError("collection busy")

            def read(self, size, timeout):
                try:
                    report = bus.queues[self.path].get(
                        timeout=min(timeout / 1000, 0.01)
                    )
                except queue.Empty:
                    return []
                if isinstance(report, Exception):
                    raise report
                return list(report)

            def close(self):
                bus.closed.append(self.path)

        return Device()


class PollTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.bus = FakeBus()
        self.entries = [entry()]
        self.enumerate = mock.Mock(side_effect=lambda vid: list(self.entries))
        for patch in (
            mock.patch.object(R, "hid", self.bus),
            mock.patch.object(
                R, "hidlist", types.SimpleNamespace(enumerate=self.enumerate)
            ),
            mock.patch.object(
                R, "time", types.SimpleNamespace(monotonic=lambda: self.now)
            ),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.provider = R.RapooProvider(listen_first=0.05)
        self.addCleanup(self.provider.close)

    def poll_until(self, predicate):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            result = self.provider.poll()
            if predicate(result):
                return result
            time.sleep(0.002)
        self.fail("provider did not receive the expected asynchronous report")

    def capture(self, report=CAPTURE_27):
        self.bus.push(report)
        return self.poll_until(lambda result: result and result[0].online)

    def test_real_receiver_capture_reaches_device_status_without_writes(self):
        st = self.capture()[0]
        self.assertEqual(
            (st.key, st.name, st.level, st.charging, st.online, st.source, st.kind),
            ("rapoo:1413", "Rapoo VT7 (Gen-2)", 27, True, True, "rapoo", "mouse"),
        )
        self.enumerate.assert_called_with(0x24AE)
        self.assertTrue(
            any(
                "07 10 04 08 07 08 07 02 1b" in line
                for line in self.provider.diagnostics()
            )
        )

    def test_reports_between_polls_are_retained(self):
        self.capture()
        self.now += 5
        self.bus.push(CAPTURE_36)
        result = self.poll_until(lambda items: items and items[0].level == 36)
        self.assertTrue(result[0].charging)
        self.assertEqual(self.bus.opened, [b"receiver"])

    def test_real_on_battery_capture_clears_charging(self):
        self.capture()
        self.bus.push(CAPTURE_42)
        result = self.poll_until(lambda items: items and items[0].level == 42)
        self.assertFalse(result[0].charging)

    def test_zero_and_full_are_valid_on_battery(self):
        for level in (0, 100):
            with self.subTest(level=level):
                self.bus.push(status(level))
                result = self.poll_until(
                    lambda items, expected=level: items and items[0].level == expected
                )
                self.assertFalse(result[0].charging)

    def test_noise_is_skipped_before_the_real_report(self):
        for report in (
            b"",
            CAPTURE_27[:8],
            bytes([6]) + CAPTURE_27[1:],
            status(27, 0),
            status(27, 3),
            status(101),
            status(255),
        ):
            self.bus.push(report)
        self.assertEqual(self.capture()[0].level, 27)

    def test_invalid_reports_alone_do_not_invent_a_level(self):
        for report in (CAPTURE_27[:8], status(27, 0), status(101)):
            self.bus.push(report)
        self.assertEqual(self.provider.poll(), [])

    def test_regular_poll_returns_without_waiting_for_a_silent_mouse(self):
        provider = R.RapooProvider()
        self.addCleanup(provider.close)
        start = time.monotonic()
        self.assertEqual(provider.poll(), [])
        self.assertLess(time.monotonic() - start, 0.2)

    def test_only_the_known_status_collection_is_opened(self):
        self.entries = [
            entry(page=1, usage=2, path=b"mouse"),
            entry(page=1, usage=6, path=b"keyboard"),
            entry(usage=14, path=b"other-vendor"),
            entry(page=0xFF01, path=b"other-page"),
            entry(pid=0x1924, path=b"other-model"),
            entry(),
            entry(),
        ]
        self.capture()
        self.assertEqual(self.bus.opened, [b"receiver"])

    def test_cable_uses_the_same_key(self):
        self.entries = [entry(b"cable", 0x4613)]
        self.bus.push(CAPTURE_27, b"cable")
        result = self.poll_until(lambda items: bool(items))
        self.assertEqual(
            (result[0].key, result[0].level, result[0].charging),
            ("rapoo:1413", 27, True),
        )

    def test_both_transports_produce_one_icon_preferring_charging(self):
        self.entries += [entry(b"cable", 0x4613)]
        self.bus.push(status(26))
        self.bus.push(CAPTURE_27, b"cable")
        result = self.poll_until(lambda items: items and items[0].charging)
        self.assertEqual(
            [(st.key, st.level, st.charging) for st in result],
            [("rapoo:1413", 27, True)],
        )

    def test_silent_cable_does_not_add_a_grey_duplicate(self):
        self.entries += [entry(b"cable", 0x4613)]
        result = self.capture()
        self.assertEqual(
            [(st.key, st.level, st.online) for st in result], [("rapoo:1413", 27, True)]
        )

    def test_short_report_gap_does_not_mean_sleep(self):
        self.capture()
        self.now += 6
        self.assertTrue(self.provider.poll()[0].online)

    def test_sleep_keeps_a_greyed_reading_that_can_wake_on_input(self):
        self.capture()
        self.now += 30
        st = self.provider.poll()[0]
        self.assertEqual((st.level, st.online, st.wake_on_input), (27, False, True))

    def test_cached_reading_expires_without_extending_its_lifetime(self):
        self.capture()
        self.now += 299
        self.assertEqual(self.provider.poll()[0].level, 27)
        self.now += 1
        self.assertEqual(self.provider.poll(), [])

    def test_a_sleeping_mouse_can_return_with_a_fresh_level(self):
        self.capture()
        self.now += 60
        self.assertFalse(self.provider.poll()[0].online)
        self.bus.push(CAPTURE_42)
        st = self.poll_until(lambda items: items and items[0].online)[0]
        self.assertEqual((st.level, st.charging), (42, False))

    def test_unplugging_clears_the_cached_level_and_closes_handle(self):
        self.capture()
        self.entries = []
        self.assertEqual(self.provider.poll(), [])
        self.assertEqual(self.bus.closed, [b"receiver"])
        self.entries = [entry()]
        self.assertEqual(self.provider.poll(), [])

    def test_unknown_rapoo_models_are_not_opened(self):
        self.entries = [entry(pid=0x4824)]
        self.assertEqual(self.provider.poll(), [])
        self.assertEqual(self.bus.opened, [])

    def test_open_failure_closes_handle_and_can_retry(self):
        self.bus.open_errors.add(b"receiver")
        self.assertEqual(self.provider.poll(), [])
        self.assertIn(b"receiver", self.bus.closed)
        self.bus.open_errors.clear()
        self.assertEqual(self.capture()[0].level, 27)

    def test_read_failure_closes_handle_and_can_retry(self):
        self.bus.push(OSError("unplugged"))
        self.assertEqual(self.provider.poll(), [])
        self.assertIn(b"receiver", self.bus.closed)
        self.assertEqual(self.capture()[0].level, 27)

    def test_close_releases_all_transports(self):
        self.entries += [entry(b"cable", 0x4613)]
        self.capture()
        self.provider.close()
        self.assertCountEqual(self.bus.closed, [b"receiver", b"cable"])

    def test_no_hid_library_is_safe(self):
        with mock.patch.object(R, "hid", None):
            self.assertEqual(self.provider.poll(), [])

    def test_enumeration_failure_is_safe(self):
        self.enumerate.side_effect = OSError("gone")
        with self.assertLogs("halo_battery", level="WARNING"):
            self.assertEqual(self.provider.poll(), [])


class RegistrationTests(unittest.TestCase):
    def test_disabling_the_provider_closes_its_listener(self):
        from test_hide_rename import hb, make_app

        app = make_app()
        provider = R.RapooProvider()
        app.providers = [provider]
        with (
            mock.patch.object(provider, "close") as close,
            mock.patch.object(hb, "save_config"),
        ):
            app.toggle_provider("rapoo")
            close.assert_called_once()
            app.toggle_provider("rapoo")
            close.assert_called_once()

    def test_quitting_the_app_closes_its_listener(self):
        from test_hide_rename import make_app

        app = make_app()
        provider = R.RapooProvider()
        app.providers = [provider]
        for name in ("stop_evt", "update_wake", "theme_evt", "bt_wake"):
            setattr(app, name, threading.Event())
        app.win_events = app.bt_watch = None
        with mock.patch.object(provider, "close") as close:
            app.quit()
            close.assert_called_once()

    def test_normal_app_and_probe_factory_include_the_provider_and_label(self):
        from test_hide_rename import hb

        for kwargs in ({}, {"jbl_listen_first": 10.0, "rapoo_listen_first": 10.0}):
            with self.subTest(kwargs=kwargs):
                providers = [
                    p for p in hb.make_providers(**kwargs) if p.name == "rapoo"
                ]
                self.assertEqual(len(providers), 1)
                self.assertIsInstance(providers[0], R.RapooProvider)
                self.assertEqual(hb.PROVIDER_LABELS["rapoo"], "Rapoo VT7 (Gen-2)")


if __name__ == "__main__":
    unittest.main()
