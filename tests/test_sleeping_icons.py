"""A sleeping device keeps its last battery ring and greys only the centre."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from halo import icons


class SleepingIconTests(unittest.TestCase):
    def test_known_level_keeps_the_same_ring_as_the_awake_device(self):
        # No badge: compare every pixel of the actual ring, including its colour,
        # track and anti-aliased edge, on both taskbar themes and near thresholds.
        for light in (False, True):
            for charging in (False, True):
                for level in (0, 1, 10, 20, 25, 30, 65, 100):
                    with self.subTest(light=light, charging=charging, level=level):
                        awake = icons.render(level, charging, True, 20, light)
                        asleep = icons.render(level, charging, False, 20, light)
                        self.assertEqual(awake.tobytes(), asleep.tobytes())

    def test_only_the_pictogram_fades_when_the_device_sleeps(self):
        for light in (False, True):
            for badge in set(icons.KINDS.values()):
                with self.subTest(light=light, badge=badge):
                    awake = icons.render(65, False, True, 20, light, badge)
                    asleep = icons.render(65, False, False, 20, light, badge)
                    changed = [(x, y) for y in range(icons.SIZE)
                               for x in range(icons.SIZE)
                               if awake.getpixel((x, y)) != asleep.getpixel((x, y))]
                    self.assertTrue(changed, "the sleeping badge must look different")
                    # The centre and its resampling fringe can change; the
                    # outside of the battery ring must not fade with it.
                    for x, y in changed:
                        self.assertLess((x + .5 - 32) ** 2 + (y + .5 - 32) ** 2, 28 ** 2)
                    self.assertTrue(any(0 < asleep.getpixel(p)[3] < awake.getpixel(p)[3]
                                        for p in changed),
                                    "the pictogram must fade but remain visible")

    def test_percentage_mode_greys_the_number_but_keeps_the_warning_ring(self):
        for light in (False, True):
            with self.subTest(light=light):
                ring = icons.render(15, False, False, 20, light)
                numbered = icons.render(15, False, False, 20, light, text="15")
                changed = [(x, y) for y in range(icons.SIZE) for x in range(icons.SIZE)
                           if ring.getpixel((x, y)) != numbered.getpixel((x, y))]
                self.assertTrue(changed, "the last percentage must remain visible")
                for x, y in changed:
                    self.assertLess((x + .5 - 32) ** 2 + (y + .5 - 32) ** 2, 25 ** 2)
                    r, g, b, alpha = numbered.getpixel((x, y))
                    self.assertEqual(r, g)
                    self.assertEqual(g, b)
                self.assertTrue(any(ring.getpixel((x, y))[0] > ring.getpixel((x, y))[1]
                                    and ring.getpixel((x, y))[3] > 200
                                    for y in range(icons.SIZE) for x in range(icons.SIZE)),
                                "the low-battery ring must stay red")

    def test_no_level_does_not_invent_a_battery_or_charging_arc_while_asleep(self):
        for light in (False, True):
            with self.subTest(light=light):
                empty = icons.render(None, False, False, 20, light, "mouse")
                stale_charging = icons.render(None, True, False, 20, light, "mouse")
                self.assertEqual(empty.tobytes(), stale_charging.tobytes())
                self.assertLess(empty.getchannel("A").getextrema()[1], 200)


if __name__ == "__main__":
    unittest.main()
