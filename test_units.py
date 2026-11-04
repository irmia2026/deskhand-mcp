from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest

from deskhand_mcp.engine import apps, input as inp, memory, overlay, scene, uia
from deskhand_mcp.engine.overlay_view import event_text, render_overlay


class SceneTests(unittest.TestCase):
    def tearDown(self):
        scene.clear()

    def test_snapshot_generation_and_window_binding(self):
        first = scene.register([{"name": "a", "x": 1, "y": 2}], "one", window_hwnd=11)
        self.assertTrue(first[0]["id"].startswith("e"))
        self.assertIsNotNone(scene.resolve(first[0]["id"]))
        self.assertTrue(scene.matches_window(11))
        self.assertFalse(scene.matches_window(12))
        second = scene.register([{"name": "b", "x": 3, "y": 4}], "two", window_hwnd=22)
        self.assertNotEqual(first[0]["id"], second[0]["id"])
        self.assertIsNone(scene.resolve(first[0]["id"]))

    def test_stable_pagination(self):
        elements = [{"name": str(i), "x": i, "y": i, "source": "cv"} for i in range(5)]
        scene.register(elements, "paged", window_hwnd=33)
        first = scene.page(2)
        self.assertEqual(len(first["elements"]), 2)
        self.assertEqual(first["total"], 5)
        cursor = first["next_cursor"]
        second = scene.page(2, cursor)
        self.assertEqual(len(second["elements"]), 2)
        self.assertNotEqual(first["elements"][0]["id"], second["elements"][0]["id"])
        third = scene.page(2, second["next_cursor"])
        self.assertEqual(len(third["elements"]), 1)
        self.assertIsNone(third["next_cursor"])
        scene.register(elements, "new", window_hwnd=34)
        self.assertFalse(scene.page(2, cursor)["ok"])


class MemoryTests(unittest.TestCase):
    def test_empty_signature_does_not_replace_valid_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            store = memory.ElementMemory(os.path.join(directory, "memory.db"))
            try:
                store.upsert("app", "target", 0.5, 0.5, "0123456789abcdef", True)
                store.upsert("app", "target", 0.6, 0.6, "", True)
                record = store.lookup("app", "target")
                self.assertEqual(record["crop_sig"], "0123456789abcdef")
                self.assertEqual(record["rel_x"], 0.6)
            finally:
                store.close()


class IdentityTests(unittest.TestCase):
    def test_same_name_at_different_place_is_rejected(self):
        hit = {"name": "Save", "type": "button", "rect": (0, 0, 100, 40),
               "automation_id": ""}
        self.assertFalse(uia._same_target(hit, "Save", (500, 500, 600, 540), "button"))

    def test_different_name_at_same_place_is_rejected(self):
        hit = {"name": "Delete", "type": "button", "rect": (0, 0, 100, 40),
               "automation_id": ""}
        self.assertFalse(uia._same_target(hit, "Save", (0, 0, 100, 40), "button"))

    def test_combined_identity_is_accepted(self):
        hit = {"name": "Save", "type": "button", "rect": (0, 0, 100, 40),
               "automation_id": "save-button"}
        self.assertTrue(uia._same_target(
            hit, "Save", (0, 0, 100, 40), "button", "save-button"))


class ValidationTests(unittest.TestCase):
    def test_invalid_mouse_button(self):
        with self.assertRaises(ValueError):
            inp.click(0, 0, button="invalid")

    def test_invalid_click_count(self):
        with self.assertRaises(ValueError):
            inp.click(0, 0, count=4)

    def test_invalid_drag_path(self):
        with self.assertRaises(ValueError):
            inp.drag_path([[1, 1]])
        with self.assertRaises(ValueError):
            inp.drag_path([[1, 1], [1, 1]])

    def test_invalid_launch_arguments(self):
        with self.assertRaises(ValueError):
            apps.launch_app("notepad.exe", "not-a-list")


class OverlayTests(unittest.TestCase):
    def test_render_transparency_and_event_privacy(self):
        label, detail = event_text("type_text", {"text": "secret-value"})
        self.assertEqual(label, "输入文本")
        self.assertNotIn("secret-value", detail)
        self.assertIn("12", detail)
        image = render_overlay((800, 600), [], now=1.0)
        self.assertEqual(image.getpixel((400, 300))[3], 0)
        self.assertGreater(image.getpixel((2, 300))[3], 0)

    def test_dpi_scale_and_primary_color(self):
        normal = render_overlay((800, 600), [], now=1.0, scale=1.0)
        scaled = render_overlay((800, 600), [], now=1.0, scale=2.0)
        self.assertEqual(normal.getpixel((400, 2))[:3], (43, 84, 229))
        self.assertGreater(normal.getpixel((0, 0))[3], 0)
        self.assertGreater(normal.getpixel((20, 2))[3], normal.getpixel((20, 40))[3])
        self.assertEqual(normal.getpixel((400, 100))[3], 0)
        self.assertGreater(scaled.getpixel((400, 100))[3], 0)

    def test_native_bgra_channel_order(self):
        from PIL import Image
        from deskhand_mcp.engine.overlay_view import premultiplied_bgra

        encoded = premultiplied_bgra(Image.new("RGBA", (1, 1), (43, 84, 229, 255)))
        self.assertEqual(encoded, bytes([229, 84, 43, 255]))
        translucent = premultiplied_bgra(Image.new("RGBA", (1, 1), (43, 84, 229, 96)))
        self.assertEqual(translucent, bytes([86, 31, 16, 96]))

    def test_pause_status_does_not_require_native_window(self):
        controller = overlay.AgentOverlay()
        controller.pause()
        self.assertTrue(controller.is_paused())
        self.assertFalse(controller.status()["visible"])
        controller.close()


class ServerPauseTests(unittest.TestCase):
    def test_paused_server_rejects_tool_before_execution(self):
        from deskhand_mcp import server

        previous = overlay._instance
        controller = overlay.AgentOverlay()
        controller.pause()
        overlay._instance = controller
        called = False

        async def action():
            nonlocal called
            called = True
            return {"ok": True}

        action.__name__ = "look"
        try:
            blocks = asyncio.run(server._wrap(action)())
            result = json.loads(blocks[0].text)
            self.assertTrue(result["paused"])
            self.assertFalse(result["ok"])
            self.assertFalse(called)
        finally:
            controller.close()
            overlay._instance = previous


class CoordinateGridTests(unittest.TestCase):
    def test_grid_uses_fixed_logical_coordinates(self):
        from PIL import Image
        from deskhand_mcp.engine import locate

        source = Image.new("RGB", (640, 360), (20, 30, 40))
        grid, metadata = locate.draw_coordinate_grid(
            source, screen_rect=(-10, 120, 630, 480))
        self.assertEqual(grid.size, source.size)
        self.assertEqual(metadata["kind"], "normalized_coordinates")
        self.assertEqual(metadata["coordinate_space"], "normalized_0_1000")
        self.assertEqual(metadata["range"], [0, 1000])
        self.assertEqual(metadata["image_size"], [640, 360])
        self.assertEqual(metadata["origin_screen"], [-10, 120])
        self.assertEqual(metadata["x_lines"][0],
                         {"value": 0, "image_x": 0, "screen_x": -10})
        self.assertEqual(metadata["x_lines"][1],
                         {"value": 100, "image_x": 64, "screen_x": 54})
        self.assertEqual(metadata["y_lines"][1],
                         {"value": 100, "image_y": 36, "screen_y": 156})
        self.assertEqual(metadata["x_lines"][-1],
                         {"value": 1000, "image_x": 639, "screen_x": 629})
        self.assertEqual(metadata["y_lines"][-1],
                         {"value": 1000, "image_y": 359, "screen_y": 479})

    def test_grid_labels_support_legacy_image_pixels(self):
        from PIL import Image
        from deskhand_mcp.engine import locate

        source = Image.new("RGB", (640, 360), (20, 30, 40))
        grid, metadata = locate.draw_coordinate_grid(
            source, screen_rect=(-10, 120, 630, 480), spacing=100,
            coordinates="image")
        self.assertEqual(grid.size, source.size)
        self.assertEqual(metadata["kind"], "image_coordinates")
        self.assertEqual(metadata["coordinate_space"], "image_pixels")
        self.assertEqual(metadata["x_lines"][1],
                         {"value": 100, "image_x": 100, "screen_x": 90})
        self.assertEqual(metadata["y_lines"][1],
                         {"value": 100, "image_y": 100, "screen_y": 220})

    def test_grid_adapts_legacy_spacing_without_resizing(self):
        from PIL import Image
        from deskhand_mcp.engine import locate

        source = Image.new("RGBA", (800, 500), (0, 0, 0, 0))
        grid, metadata = locate.draw_coordinate_grid(source, coordinates="image")
        self.assertEqual(grid.size, source.size)
        self.assertEqual(grid.mode, "RGBA")
        self.assertEqual(metadata["spacing_px"], 100)
        self.assertEqual(metadata["screen_rect"], [0, 0, 800, 500])
        self.assertEqual(metadata["x_lines"][-1]["image_x"], 700)
        self.assertEqual(metadata["y_lines"][-1]["image_y"], 400)
        with self.assertRaises(ValueError):
            locate.draw_coordinate_grid(source, spacing=0)


if __name__ == "__main__":
    unittest.main()
