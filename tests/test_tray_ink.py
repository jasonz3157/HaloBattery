"""Tray contrast and small-stroke coverage, without hardware or native windows."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sandbox
_sandbox.install_guards()
from halo import icons, icon_artwork


class TrayInkTests(unittest.TestCase):
    def test_small_ring_keeps_its_geometric_coverage(self):
        # Annulus with the accepted 7.875-logical-pixel outer radius and 1px
        # stroke. Alpha ringing must not add the old 8-11% extra ink at 16/20px.
        logical_area = math.pi * (7.875 ** 2 - 6.875 ** 2)
        for light in (False, True):
            for size in (16, 20, 24, 32, 40, 48, 64):
                with self.subTest(light=light, size=size):
                    image = icons.render(100, False, True, light_taskbar=light, size=size)
                    histogram = image.getchannel('A').histogram()
                    coverage = sum(alpha * count for alpha, count in enumerate(histogram)) / icons.NEUTRAL_OPACITY
                    expected = logical_area * (size / 16) ** 2
                    self.assertAlmostEqual(coverage, expected, delta=max(.5, expected * .025))

    def test_neutral_ink_stays_soft_in_all_states_and_themes(self):
        for light in (False, True):
            foreground = (0, 0, 0) if light else (255, 255, 255)
            for level, charging, online, forced in (
                    (100, False, True, False), (None, False, True, False),
                    (None, False, True, True), (100, False, False, False),
                    (None, True, True, False), (0, False, True, False)):
                for kind in icons.PICTOGRAM_KINDS:
                    with self.subTest(light=light, kind=kind, level=level,
                                      charging=charging, online=online, forced=forced):
                        image = icons.render(level, charging, online, light_taskbar=light,
                                             badge=kind, force_active=forced, size=20)
                        pixels = (image.getpixel((x, y)) for y in range(image.height)
                                  for x in range(image.width))
                        neutral = [a for r, g, b, a in pixels
                                   if (r, g, b) == foreground]
                        self.assertTrue(neutral)
                        self.assertLessEqual(max(neutral), icons.NEUTRAL_OPACITY)

    def test_sleep_keeps_every_ring_pixel_and_greys_the_pictogram(self):
        styles = (("mouse", "default"), ("gamepad", "xbox"),
                  ("gamepad", "playstation"), ("gamepad", "nintendo"))
        for light in (False, True):
            for level in (None, 0, 10, 25, 65, 100):
                ring = icons.render(level, False, True, light_taskbar=light, size=64)
                points = [(x, y) for y in range(ring.height) for x in range(ring.width)]
                ring_pixels = [ring.getpixel(point) for point in points]
                for badge, brand in styles:
                    with self.subTest(light=light, level=level, badge=badge, brand=brand):
                        options = dict(light_taskbar=light, size=64, badge=badge,
                                       brand=brand, brand_color=True)
                        awake = icons.render(level, False, True, **options)
                        asleep = icons.render(level, False, False, **options)
                        self.assertEqual(awake.getchannel("A").tobytes(),
                                         asleep.getchannel("A").tobytes())
                        asleep_pixels = [asleep.getpixel(point) for point in points]
                        self.assertEqual(
                            [pixel for pixel, original in zip(asleep_pixels, ring_pixels)
                             if original[3]],
                            [pixel for pixel in ring_pixels if pixel[3]],
                        )
                        centre = {pixel[:3] for pixel, original in zip(asleep_pixels, ring_pixels)
                                  if pixel[3] and not original[3]}
                        self.assertEqual(centre, {(128, 128, 128)})

    def test_sleep_keeps_percentage_and_badge_free_icons_identical(self):
        for light in (False, True):
            for level in (0, 10, 25, 65, 100):
                for text in ("", str(level)):
                    with self.subTest(light=light, level=level, text=text):
                        options = dict(light_taskbar=light, text=text, size=20)
                        awake = icons.render(level, False, True, **options)
                        asleep = icons.render(level, False, False, **options)
                        self.assertEqual(awake.tobytes(), asleep.tobytes())

    def test_brand_colors_keep_exact_pixels_and_dim_alpha(self):
        for foreground in ((0, 0, 0), (255, 255, 255)):
            for brand in ('xbox', 'playstation', 'nintendo'):
                for alpha in (255, 110):
                    with self.subTest(foreground=foreground, brand=brand, alpha=alpha):
                        original = icon_artwork.glyph('gamepad', 32, foreground, alpha, brand, True)
                        softened = icon_artwork.glyph('gamepad', 32, foreground, alpha, brand, True,
                                                     foreground_opacity=icons.NEUTRAL_OPACITY)
                        self.assertEqual(softened.tobytes(), original.tobytes())

    def test_battery_signal_colors_keep_full_strength(self):
        for light in (False, True):
            for level, charging, color in ((100, True, icons.GREEN),
                                           (10, False, icons.RED), (25, False, icons.AMBER)):
                with self.subTest(light=light, level=level, charging=charging):
                    image = icons.render(level, charging, True, light_taskbar=light, size=64)
                    pixels = (image.getpixel((x, y)) for y in range(image.height)
                              for x in range(image.width))
                    opacity = max(a for r, g, b, a in pixels if (r, g, b) == color)
                    self.assertEqual(opacity, 255)


if __name__ == '__main__':
    unittest.main()
