"""Sleep changes the centre colour while preserving cached rings and glyph coverage."""
import unittest

from PIL import Image, ImageDraw

from halo import icons


class ColoredGlyph:
    def render(self, size, foreground, alpha=255, foreground_opacity=230/255):
        image = Image.new("RGBA", (size, size))
        ImageDraw.Draw(image).rectangle(
            (size*.4, size*.4, size*.6, size*.6), fill=(221, 46, 68, alpha))
        return image


class SleepingIconTests(unittest.TestCase):
    def test_sleep_preserves_rings_for_both_styles_and_filled_icons(self):
        for style in ("thin", "classic"):
            for filled in (False, True):
                for light in (False, True):
                    for size in (16, 24, 64):
                        for level in (None, 0, 10, 25, 65, 100):
                            with self.subTest(style=style, filled=filled, light=light,
                                              size=size, level=level):
                                options = dict(ring_style=style, filled=filled,
                                               light_taskbar=light, size=size)
                                awake_ring = icons.render(level, False, True, **options)
                                asleep_ring = icons.render(level, False, False, **options)
                                self.assertEqual(awake_ring.tobytes(), asleep_ring.tobytes())
                                awake = icons.render(level, False, True, badge="mouse", **options)
                                asleep = icons.render(level, False, False, badge="mouse", **options)
                                self.assertEqual(awake.getchannel("A").tobytes(),
                                                 asleep.getchannel("A").tobytes())
                                for y in range(size):
                                    for x in range(size):
                                        ring = awake_ring.getpixel((x, y))
                                        pixel = asleep.getpixel((x, y))
                                        radius_squared = (x+.5-size/2)**2 + (y+.5-size/2)**2
                                        if radius_squared >= (7*size/16)**2:
                                            self.assertEqual(pixel, ring)
                                        elif pixel[3] and not ring[3]:
                                            self.assertEqual(pixel[:3], (128, 128, 128))

    def test_colored_custom_icons_turn_grey_and_recover_on_wake(self):
        for style in ("thin", "classic"):
            for light in (False, True):
                options = dict(ring_style=style, light_taskbar=light, size=32,
                               custom_glyph=ColoredGlyph())
                awake = icons.render(65, False, True, **options)
                asleep = icons.render(65, False, False, **options)
                self.assertEqual(awake.getchannel("A").tobytes(),
                                 asleep.getchannel("A").tobytes())
                self.assertEqual(awake.getpixel((16, 16)), (221, 46, 68, 255))
                self.assertEqual(asleep.getpixel((16, 16)), (128, 128, 128, 255))
                self.assertEqual(icons.render(65, False, True, **options).tobytes(),
                                 awake.tobytes())
