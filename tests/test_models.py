import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from higgsfield import models  # noqa: E402


class Bodies(unittest.TestCase):
    def test_seedance_text_to_video(self):
        endpoint, body = models.seedance_25(" a cat ", 5, "720p", "9:16", True)
        self.assertEqual(endpoint, "bytedance/seedance-2.5/text-to-video")
        self.assertEqual(body, {"duration": 5, "resolution": "720p", "generate_audio": True,
                                "prompt": "a cat", "aspect_ratio": "9:16"})

    def test_seedance_image_to_video_drops_aspect_ratio_and_empty_prompt(self):
        endpoint, body = models.seedance_25("", 5, "480p", "16:9", False, "https://i/a.png")
        self.assertEqual(endpoint, "bytedance/seedance-2.5/image-to-video")
        self.assertNotIn("aspect_ratio", body)
        self.assertNotIn("prompt", body)
        self.assertNotIn("end_image_url", body)

    def test_kling_first_last_frame(self):
        endpoint, body = models.kling_40("turn", 10, "1080p", "16:9", "https://i/a.png", "https://i/b.png")
        self.assertEqual(endpoint, "kling-video/v4.0/image-to-video")
        self.assertEqual(body["end_image_url"], "https://i/b.png")

    def test_needs_prompt_or_image(self):
        with self.assertRaises(ValueError):
            models.kling_40("  ", 5, "720p", "16:9")
        with self.assertRaises(ValueError):
            models.seedance_25("x", 5, "720p", "16:9", True, None, "https://i/b.png")

    def test_genjutsu(self):
        endpoint, body = models.genjutsu_motion_transfer("genjutsu-light", "https://v", ["https://i"], "", "720p")
        self.assertEqual(endpoint, "higgsfield/genjutsu-light/motion-transfer/v1.0")
        self.assertEqual(body["prompt"], "")
        with self.assertRaises(ValueError):
            models.genjutsu_motion_transfer("genjutsu", "https://v", ["https://i"] * 9, "", "720p")


class Prices(unittest.TestCase):
    def test_list_price(self):
        self.assertEqual(models.list_price(models.SEEDANCE_RATES, "720p", 5), 1.3235)
        self.assertEqual(models.list_price(models.KLING4_RATES, "1080p", 10), 1.879)

    def test_rates_file_covers_every_option(self):
        self.assertEqual(set(models.SEEDANCE_RATES), {"480p", "720p", "1080p"})
        self.assertEqual(set(models.KLING4_RATES), {"720p", "1080p"})
        self.assertEqual(set(models.GENJUTSU_RATES), set(models.GENJUTSU_ENDPOINTS))


if __name__ == "__main__":
    unittest.main()
