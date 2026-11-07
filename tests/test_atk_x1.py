"""ATK X1 Ultimate 2.0 protocol and connection transitions, using simulated HID.

The ids and request come from atk-tool-plus. These are constructed replies,
not captures from the locally connected mouse (HID access is sandboxed).
"""
import types
import unittest
from unittest import mock

from test_pulsar import FakeBus, reply
from providers import pulsar as P


class AtkX1Test(unittest.TestCase):
    def setUp(self):
        self.buses = []
        self.lengths = {}
        self.provider = P.PulsarProvider()
        combined = FakeBus(P.ATK_X1_VID, P.ATK_X1_RECEIVER)
        combined.cols = []
        self.combined = combined
        base_device = combined.device_class()

        class FakeDevice(base_device):
            def open_path(self, path):
                super().open_path(path)
                self.asked = False

            def write(self, frame):
                super().write(frame)
                self.asked = True

            def read(self, length, timeout_ms=0):
                # A new handle has no queued reply before its query, even if a
                # previous poll wrote to this same collection.
                return super().read(length, timeout_ms) if self.asked else b""

        for name, value in (
            ("hidlist", types.SimpleNamespace(enumerate=self.enumerate)),
            ("hid", types.SimpleNamespace(device=FakeDevice)),
            ("output_length", lambda path: self.lengths.get(path, 17)),
        ):
            patch = mock.patch.object(P, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def enumerate(self, vid):
        return [entry for bus in self.buses for entry in bus.enumerate(vid)]

    def add(self, pid, replies=(), **kw):
        bus = FakeBus(P.ATK_X1_VID, pid, replies, **kw)
        self.buses.append(bus)
        self.combined.cols.extend(bus.cols)
        return bus

    def assert_reading(self, level, charging=False, online=True):
        found = self.provider.poll()
        self.assertEqual(1, len(found))
        status = found[0]
        self.assertEqual(
            ("pulsar:373b11e7", "ATK X1 Ultimate 2.0", level, charging,
             online, "pulsar", "mouse", not online),
            (status.key, status.name, status.level, status.charging,
             status.online, status.source, status.kind, status.wake_on_input),
        )
        return status

    def test_receiver_reads_percentage_with_the_documented_query(self):
        bus = self.add(P.ATK_X1_RECEIVER, [reply(65, 0, 3900)])
        self.assert_reading(65)
        written = [col for col in bus.cols if col.sent]
        self.assertEqual(1, len(written))
        self.assertEqual([[0x08, 0x04] + [0] * 14 + [0x49]], written[0].sent)
        self.assertEqual([(0xFF02, 2)], bus.written())

    def test_cable_reads_percentage_and_charging(self):
        self.add(P.ATK_X1_WIRED, [reply(71, 1, 4100)])
        self.assert_reading(71, charging=True)

    def test_cable_wins_and_receiver_is_not_queried(self):
        receiver = self.add(P.ATK_X1_RECEIVER, [reply(65)])
        self.add(P.ATK_X1_WIRED, [reply(71, 1)])
        self.assert_reading(71, charging=True)
        self.assertEqual([], receiver.written())

    def test_silent_cable_falls_back_to_receiver(self):
        self.add(P.ATK_X1_WIRED)
        self.add(P.ATK_X1_RECEIVER, [reply(65)])
        self.assert_reading(65)

    def test_changing_connections_keeps_the_same_icon_key(self):
        receiver = self.add(P.ATK_X1_RECEIVER, [reply(65)])
        before = self.assert_reading(65)
        self.add(P.ATK_X1_WIRED, [reply(71, 1)])
        charging = self.assert_reading(71, charging=True)
        self.buses = [receiver]
        receiver.cols[5].replies.append(reply(72))
        after = self.assert_reading(72)
        self.assertEqual(before.key, charging.key)
        self.assertEqual(before.key, after.key)
        self.assertEqual([before.key], list(self.provider._last))

    def test_silent_receiver_keeps_reading_and_input_can_wake_it(self):
        self.add(P.ATK_X1_RECEIVER, [reply(65)])
        self.assert_reading(65)
        with mock.patch.object(P.time, "time", return_value=10**12):
            self.assert_reading(65, online=False)

    def test_sleeping_mouse_is_not_invented_before_any_valid_reading(self):
        self.add(P.ATK_X1_RECEIVER)
        self.assertEqual([], self.provider.poll())

    def test_removing_both_connections_clears_the_reading(self):
        self.add(P.ATK_X1_RECEIVER, [reply(65)])
        self.assert_reading(65)
        self.buses.clear()
        self.assertEqual([], self.provider.poll())
        self.assertEqual({}, self.provider._last)

    def test_invalid_checksum_never_overwrites_last_level(self):
        bus = self.add(P.ATK_X1_RECEIVER, [reply(65)])
        self.assert_reading(65)
        bus.cols[5].replies.append(reply(99, break_checksum=True))
        self.assert_reading(65, online=False)

    def test_event_short_and_out_of_range_replies_are_ignored(self):
        self.add(P.ATK_X1_RECEIVER, [reply(90, command=0x0A),
                                         b"\x08\x04\x00", reply(101), reply(65)])
        self.assert_reading(65)

    def test_zero_and_full_battery_are_valid(self):
        bus = self.add(P.ATK_X1_RECEIVER, [reply(0)])
        self.assert_reading(0)
        bus.cols[5].replies.append(reply(100))
        self.assert_reading(100)

    def test_ff04_is_used_when_ff02_cannot_carry_the_query(self):
        bus = self.add(P.ATK_X1_RECEIVER, [reply(65)], answering=(0xFF04, 2))
        for col in bus.cols:
            if col.info["usage_page"] == 0xFF02:
                self.lengths[col.info["path"]] = 64
        self.assert_reading(65)
        self.assertEqual([(0xFF04, 2)], bus.written())

    def test_absent_control_collections_never_receive_a_query(self):
        bus = self.add(P.ATK_X1_RECEIVER, [reply(65)],
                       shape=[(0, 1, 2), (2, 1, 6), (1, 0xFF05, 0)])
        self.assertEqual([], self.provider.poll())
        self.assertTrue(all(col.opened == 0 for col in bus.cols))
        self.assertIn("nothing sent", "\n".join(self.provider.diagnostics()))

    def test_control_collections_with_incompatible_lengths_are_not_opened(self):
        bus = self.add(P.ATK_X1_RECEIVER, [reply(65)])
        for col in bus.cols:
            self.lengths[col.info["path"]] = 64
        self.assertEqual([], self.provider.poll())
        self.assertTrue(all(col.opened == 0 for col in bus.cols))

    def test_unknown_report_length_still_allows_the_known_control_collection(self):
        bus = self.add(P.ATK_X1_RECEIVER, [reply(65)])
        for col in bus.cols:
            self.lengths[col.info["path"]] = None
        self.assert_reading(65)
        self.assertEqual([(0xFF02, 2)], bus.written())

    def test_unlisted_atk_id_is_never_queried(self):
        bus = self.add(0x11E8, [reply(65)])
        self.assertEqual([], self.provider.poll())
        self.assertEqual([], bus.written())

    def test_existing_atk_mouse_keeps_its_separate_icon(self):
        self.add(P.ATK_X1_RECEIVER, [reply(65)])
        self.add(0x1085, [reply(40)])
        found = self.provider.poll()
        self.assertEqual({"pulsar:373b1085", "pulsar:373b11e7"}, {s.key for s in found})


if __name__ == "__main__":
    unittest.main()
