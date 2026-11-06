"""Regression coverage for target boundaries and model-independent grounding."""

import asyncio
import io
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from deskhand_mcp import rw, server, tools
from deskhand_mcp.engine import desktop, locate, ocr, overlay, scene, uia, verify
from deskhand_mcp.engine import input as inp
from deskhand_mcp.overlay_service import OverlayService


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGB", (900, 600), "black")
        self.items = scene.register(
            [
                {
                    "name": "保存(&S)",
                    "source": "uia",
                    "type": "button",
                    "x": -350,
                    "y": 230,
                    "left": -400,
                    "top": 210,
                    "right": -300,
                    "bottom": 250,
                },
                {
                    "name": "",
                    "source": "cv",
                    "type": "box",
                    "x": 0,
                    "y": 500,
                    "left": -20,
                    "top": 480,
                    "right": 20,
                    "bottom": 520,
                    "visual_hint": {"color": "green", "shape": "circle"},
                },
                {
                    "name": "保存(&S)",
                    "source": "uia",
                    "type": "button",
                    "x": 250,
                    "y": 230,
                    "left": 200,
                    "top": 210,
                    "right": 300,
                    "bottom": 250,
                },
            ],
            "Fixture",
            self.image,
            (-500, 200),
            window_hwnd=11,
        )

    def tearDown(self):
        scene.clear()
        self.image.close()

    def test_negative_origin_scaled_mapping(self):
        mapping = scene.register_view((-400, 210, -300, 250), (300, 120))
        self.assertEqual(scene.image_point(mapping["image_id"], 150, 60), (-350, 230))

    def test_expired_image_id_rejected(self):
        mapping = scene.register_view((-400, 210, -300, 250), (300, 120))
        scene.register([], "New", window_hwnd=11)
        with self.assertRaises(ValueError):
            scene.image_point(mapping["image_id"], 10, 10)

    def test_outside_image_and_nonfinite_rejected(self):
        m = scene.register_view((0, 0, 100, 100), (100, 100))
        for x, y in ((100, 0), (-1, 0), (float("nan"), 0)):
            with self.assertRaises(ValueError):
                scene.image_point(m["image_id"], x, y)

    def test_ambiguous_names_do_not_auto_choose(self):
        result = asyncio.run(tools.find_element(query="保存", image=False))
        self.assertEqual(result["total"], 2)
        self.assertTrue(result["ambiguous"])
        self.assertNotIn("suggested_click", result)

    def test_filter_unnamed_green_circle_without_vision(self):
        result = asyncio.run(
            tools.find_element(color="绿色", shape="圆形", image=False)
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["suggested_click"], {"element": self.items[1]["id"]})

    def test_name_query_searches_beyond_first_page(self):
        scene.register(
            [
                {"name": str(i), "id": str(i), "x": i, "y": 0, "source": "uia"}
                for i in range(90)
            ],
            shot=self.image,
            window_hwnd=11,
        )
        r = asyncio.run(tools.find_element(query="89", image=False))
        self.assertEqual(r["candidates"][0]["name"], "89")

    def test_recursive_crop_mapping_and_focus(self):
        a = asyncio.run(tools.inspect_region(element=self.items[0]["id"], zoom=3))
        p = a["focus_point"]
        self.assertEqual(
            scene.image_point(a["image_id"], p["image_x"], p["image_y"]), (-350, 230)
        )
        b = asyncio.run(tools.inspect_region(image_id=a["image_id"], cell="B2"))
        self.assertGreater(b["screen_rect"][0], a["screen_rect"][0])
        self.assertLess(b["screen_rect"][2], a["screen_rect"][2])

    def test_clear_removes_views(self):
        a = scene.register_view((0, 0, 10, 10), (10, 10))
        scene.clear()
        with self.assertRaises(ValueError):
            scene.view(a["image_id"])

    def test_exact_matches_exclude_unrelated_fuzzy_results(self):
        result = asyncio.run(
            tools.find_element(query="保存", control_type="button", image=False)
        )
        self.assertEqual(result["total"], 2)
        self.assertTrue(all(c["match_type"] == "exact" for c in result["candidates"]))

    def test_root_thumbnail_mapping_matches_actual_png(self):
        page = scene.page(2)
        result = tools._present_look_page(
            {"ok": True},
            page,
            scene.current_image(),
            scene.current_origin(),
            True,
            "raw",
            512,
        )
        with Image.open(io.BytesIO(result["_image"])) as png:
            self.assertEqual(list(png.size), result["image_size"])
            self.assertLessEqual(max(png.size), 512)
        w, h = result["image_size"]
        self.assertEqual(
            scene.image_point(result["image_id"], w / 2, h / 2), (-50, 500)
        )

    def test_grid_thumbnail_mapping_and_labels(self):
        result = tools._present_look_page(
            {"ok": True}, scene.page(2), scene.current_image(),
            scene.current_origin(), True, "grid", 512,
        )
        with Image.open(io.BytesIO(result["_image"])) as png:
            self.assertEqual(list(png.size), result["image_size"])
        self.assertEqual(result["grid"]["image_size"], result["image_size"])
        self.assertEqual(result["grid"]["coordinate_space"], "normalized_0_1000")
        self.assertEqual(result["image_mapping"]["coordinate_space"], "normalized_0_1000")
        self.assertIn("无需", result["image_note"])
        self.assertEqual(scene.image_point(result["image_id"], 500, 500), (-50, 500))
        self.assertEqual(scene.image_point(result["image_id"], 1000, 1000), (399, 799))
        for value in (-1, 1001):
            with self.assertRaises(ValueError):
                scene.image_point(result["image_id"], value, 500)
        labels = result["grid"]["labels"]
        self.assertTrue(labels)
        for i, first in enumerate(labels):
            for second in labels[i + 1:]:
                a, b = first["rect"], second["rect"]
                self.assertTrue(
                    a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3],
                    (first, second),
                )
        legacy = tools._present_look_page(
            {"ok": True}, scene.page(2), scene.current_image(),
            scene.current_origin(), True, "grid", 512, "image",
        )
        self.assertEqual(legacy["image_mapping"]["coordinate_space"], "image_pixels")
        w, h = legacy["image_size"]
        self.assertEqual(scene.image_point(legacy["image_id"], w / 2, h / 2), (-50, 500))

    def test_image_point_rejects_local_layout_change(self):
        mapping = scene.register_view((-500, 200, 400, 800), (900, 600))
        with patch.object(desktop, "screenshot", return_value=self.image.copy()):
            desktop._validate_image_points(mapping["image_id"], [(-50, 500)])
        crop = scene.register_view((-300, 400, -100, 600), (200, 200))
        with patch.object(desktop, "screenshot", return_value=self.image.copy()):
            desktop._validate_image_points(crop["image_id"], [(-200, 500)])
        changed = self.image.copy()
        changed.paste("white", (443, 293, 458, 308))
        with patch.object(desktop, "screenshot", return_value=changed):
            with self.assertRaises(rw.RwError) as caught:
                desktop._validate_image_points(mapping["image_id"], [(-50, 500)])
        self.assertIn("stale", str(caught.exception))

    def test_same_luminance_different_colors_detected(self):
        a = Image.new("RGB", (10, 10), (255, 0, 0))
        b = Image.new("RGB", (10, 10), (0, 130, 0))
        self.assertTrue(verify.diff_images(a, b)["changed"])
        a.close()
        b.close()


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        scene.clear()

    async def test_missing_window_cannot_dispatch_key(self):
        with (
            patch.object(desktop, "run", new=AsyncMock(return_value=None)),
            patch.object(inp, "press") as press,
        ):
            r = await tools.press_key(["ctrl", "s"], window="missing")
            self.assertFalse(r["ok"])
            press.assert_not_called()

    async def test_expired_binding_is_not_foreground_fallback(self):
        scene.register([], window_hwnd=11)
        with (
            patch.object(scene, "_expired", return_value=True),
            patch.object(desktop, "run", new=AsyncMock()) as run,
        ):
            r = await tools.press_key(["a"])
            self.assertFalse(r["ok"])
            run.assert_not_called()

    async def test_never_type_into_unrelated_frontmost_window(self):
        scene.register([], window_hwnd=11)

        async def run(fn, *args):
            if fn is desktop.window_from_hwnd:
                return {"hwnd": 11}
            if fn is desktop.foreground_window:
                return {"hwnd": 99}
            if fn is desktop.same_window_family:
                return False
            raise AssertionError("unexpected input")

        with patch.object(desktop, "run", side_effect=run):
            result = await tools.type_text("must not type", focus="never")
            self.assertFalse(result["ok"])

    async def test_status_does_not_resume_physical_pause(self):
        controller = MagicMock()
        controller.is_paused.return_value = True
        controller.status.return_value = {"ok": True, "paused": True}
        service = object.__new__(OverlayService)
        service.controller = controller
        service.user_paused = False
        self.assertFalse(service._begin({"tool": "look"})["ok"])
        controller.resume.assert_not_called()
        with patch.object(overlay, "get_overlay", return_value=controller):
            r = await server._wrap(tools.agent_control)(action="status")
        self.assertTrue(json.loads(r[0].text)["paused"])
        controller.begin.assert_not_called()
        controller.resume.assert_not_called()

    async def test_complete_actions_are_serialized(self):
        controller = MagicMock()
        controller.is_paused.return_value = False
        controller.begin.return_value = 1
        order = []

        async def action(label):
            order.append(label + " focus")
            await asyncio.sleep(0.01)
            order.append(label + " input")
            return {"ok": True}

        with (
            patch.object(overlay, "get_overlay", return_value=controller),
            patch.object(server, "_ACTION_LOCK", asyncio.Lock()),
        ):
            await asyncio.gather(
                server._wrap(action)(label="A"), server._wrap(action)(label="B")
            )
        self.assertEqual(order, ["A focus", "A input", "B focus", "B input"])

    async def test_dispatch_rechecks_before_input(self):
        async def run(fn, *args):
            return fn(*args)

        with (
            patch.object(desktop, "run", side_effect=run),
            patch.object(
                desktop, "validate_input_target", side_effect=rw.RwError("wrong app")
            ),
            patch.object(inp, "click") as click,
        ):
            with self.assertRaises(rw.RwError):
                await tools._dispatch_real(
                    {"hwnd": 1}, inp.click, 900, 900, points=[(900, 900)]
                )
            click.assert_not_called()

    async def test_typing_button_cannot_activate_it(self):
        el = scene.register(
            [{"name": "Send", "type": "button", "source": "uia", "x": 1, "y": 2}],
            window_hwnd=11,
        )[0]
        with patch.object(tools, "click", new=AsyncMock()) as click:
            result = await tools.type_text("hello", element=el["id"])
        self.assertFalse(result["ok"])
        click.assert_not_called()

    async def test_confirmed_uia_action_updates_memory(self):
        el = scene.register(
            [{"name": "查看", "type": "menu", "source": "uia", "x": 40, "y": 20,
              "left": 20, "top": 10, "right": 60, "bottom": 30}],
            window_hwnd=11,
        )[0]
        win = {"hwnd": 11, "title": "Fixture", "rect": (0, 0, 100, 100)}

        async def run(fn, *args, **kwargs):
            if fn is desktop.window_from_hwnd:
                return win
            if fn is uia.invoke_at:
                return {"ok": True, "state_changed": True,
                        "state_before": {"expandcollapse": 0},
                        "state_after": {"expandcollapse": 1}, "tree_changed": True}
            raise AssertionError(fn)

        with (
            patch.object(tools, "_apply_focus", new=AsyncMock(return_value=(win, False, None))),
            patch.object(desktop, "run", side_effect=run),
            patch.object(tools, "_update_memory") as update,
        ):
            result = await tools.element_action(el["id"], "expand")
        self.assertTrue(result["verified"])
        update.assert_called_once()
        self.assertEqual(update.call_args.args[1], "查看")
        self.assertIs(update.call_args.args[0]["win"], win)
        self.assertIsNone(update.call_args.args[0]["shot"])

    async def test_launch_wait_does_not_choose_an_unrelated_new_window(self):
        unrelated = {"hwnd": 2, "rect": (0, 0, 100, 100)}
        target = {"hwnd": 3, "rect": (0, 0, 100, 100)}
        seen = 0

        async def run(fn, *args):
            nonlocal seen
            if fn is desktop.enum_windows:
                seen += 1
                return [] if seen == 1 else [unrelated, target]
            if fn is tools.apps.launch_app:
                return {"ok": True, "method": "executable", "launcher_pid": 42}
            if fn is desktop.window_pid:
                return 99 if args[0] == 2 else 42
            raise AssertionError(fn)

        with patch.object(desktop, "run", side_effect=run):
            result = await tools.launch_app("fixture.exe", wait_for_window=True)
        self.assertTrue(result["ready"])
        self.assertEqual(result["window"]["hwnd"], 3)

    async def test_tool_result_contains_exit_hint(self):
        controller = MagicMock()
        controller.is_paused.return_value = False
        controller.begin.return_value = 1

        async def action():
            return {"ok": True}

        with patch.object(overlay, "get_overlay", return_value=controller):
            blocks = await server._wrap(action)()
        result = json.loads(blocks[0].text)
        self.assertIn("agent_control(action='exit')", result["agent_exit_hint"])
        self.assertIn("结束对话", result["agent_exit_hint"])
        self.assertIn("完成全部电脑操作后必须调用 exit", tools.agent_control.__doc__)

    async def test_stale_input_error_is_structured(self):
        controller = MagicMock()
        controller.is_paused.return_value = False
        controller.begin.return_value = 1

        async def action():
            raise rw.RwError("stale：图片落点已变化")

        with patch.object(overlay, "get_overlay", return_value=controller):
            blocks = await server._wrap(action)()
        result = json.loads(blocks[0].text)
        self.assertFalse(result["ok"])
        self.assertTrue(result["stale"])

    async def test_text_only_host_never_receives_image_blocks(self):
        controller = MagicMock()
        controller.is_paused.return_value = False
        controller.begin.return_value = 1

        async def action():
            return {"ok": True, "_image": b"not-emitted"}

        with (
            patch.object(overlay, "get_overlay", return_value=controller),
            patch.dict(os.environ, {"DESKHAND_IMAGE_OUTPUT": "off"}),
        ):
            blocks = await server._wrap(action)()
        self.assertTrue(all(b.type == "text" for b in blocks))
        self.assertIn("image_omitted", json.loads(blocks[0].text))

    async def test_large_image_stays_below_stdio_line_limit(self):
        import base64
        import random

        source = Image.frombytes(
            "RGB", (1280, 800), random.Random(42).randbytes(1280 * 800 * 3))
        buf = io.BytesIO()
        source.save(buf, format="PNG")
        png = buf.getvalue()
        self.assertGreater(4 * ((len(png) + 2) // 3), 1_048_576)

        async def action():
            return {"ok": True, "image_size": [1280, 800], "_image": png}

        controller = MagicMock()
        controller.is_paused.return_value = False
        controller.begin.return_value = 1
        with patch.object(overlay, "get_overlay", return_value=controller):
            blocks = await server._wrap(action)()

        self.assertEqual(blocks[1].mime_type, "image/jpeg")
        response = json.dumps({
            "jsonrpc": "2.0", "id": 3,
            "result": {"content": [
                block.model_dump(mode="json", by_alias=True) for block in blocks
            ]},
        }, ensure_ascii=False, separators=(",", ":"))
        self.assertLess(len(response.encode("utf-8")), 1_048_576)
        with Image.open(io.BytesIO(base64.b64decode(blocks[1].data))) as image:
            self.assertEqual(image.size, (1280, 800))


class CancellationTests(unittest.TestCase):
    def test_cancelled_drag_always_releases_mouse(self):
        api = MagicMock()
        constants = MagicMock(MOUSEEVENTF_LEFTDOWN=2, MOUSEEVENTF_LEFTUP=4)
        with (
            patch.object(inp, "_win32", return_value=(api, constants)),
            patch.object(inp, "move_to"),
            patch.object(inp.time, "sleep"),
        ):
            with self.assertRaises(inp.InputCancelled):
                inp.drag_path([[10, 10], [20, 20]], should_cancel=lambda: True)
        flags = [call.args[0] for call in api.mouse_event.call_args_list]
        self.assertEqual(flags, [2, 4])


class CollectionTests(unittest.TestCase):
    def test_first_page_keeps_all_channels_without_losing_items(self):
        channels = {
            name: [{"source": name, "name": str(i)} for i in range(count)]
            for name, count in (("uia", 80), ("ocr", 15), ("cv", 10))
        }
        balanced = tools._balanced_elements(channels)
        self.assertEqual(len(balanced), 105)
        self.assertEqual({e["source"] for e in balanced[:5]}, {"uia", "ocr", "cv"})

    def test_duplicate_ocr_labels_are_not_automatic_coordinates(self):
        words = [
            {"text": "保存", "cx": 50, "cy": 50},
            {"text": "保存", "cx": 250, "cy": 50},
        ]
        self.assertIsNone(ocr.find_text(words, "保存"))
        self.assertEqual(len(ocr.find_text_candidates(words, "保存")), 2)

    def test_duplicate_window_titles_require_hwnd(self):
        wins = [
            {"hwnd": i, "title": "Fixture", "rect": (0, 0, 100, 100), "iconic": False}
            for i in (1, 2)
        ]
        with patch.object(desktop, "enum_windows", return_value=wins):
            with self.assertRaises(ValueError):
                desktop.find_window("Fixture")

    def test_ocr_preserves_named_uia_control_and_names_unnamed_control(self):
        named = {
            "name": "高度", "type": "control", "x": 100, "y": 100,
            "left": 80, "top": 80, "right": 120, "bottom": 120,
        }
        unnamed = {
            "name": "", "type": "control", "x": 300, "y": 100,
            "left": 280, "top": 80, "right": 320, "bottom": 120,
        }
        words = [
            {"name": "609", "type": "text", "x": 100, "y": 100,
             "left": 80, "top": 80, "right": 120, "bottom": 120},
            {"name": "800", "type": "text", "x": 300, "y": 100,
             "left": 280, "top": 80, "right": 320, "bottom": 120},
        ]
        remaining = tools._dedupe_ocr_uia(words, [named, unnamed])
        self.assertEqual(named["name"], "高度")
        self.assertEqual(named["ocr_text"], "609")
        self.assertEqual(unnamed["name"], "800")
        self.assertEqual(remaining, [])

    def test_uia_can_collect_all_for_pagination(self):
        controls = [
            {
                "name": str(i),
                "type": "button",
                "rect": (10, i * 10, 90, i * 10 + 8),
                "pats": {"invoke": True},
                "automation_id": str(i),
            }
            for i in range(70)
        ]
        collected = {
            "controls": controls,
            "truncated": False,
            "visited": 70,
            "elapsed": 0.01,
            "chrome_skipped": 0,
            "truncated_reason": "",
        }
        with (
            patch.object(uia, "available", return_value=True),
            patch.object(uia, "_is_minimized", return_value=False),
            patch.object(uia, "_cache_key", return_value=("test", "test")),
            patch.object(uia, "_collect_controls", return_value=collected),
            patch.object(desktop, "screen_bounds", return_value=(0, 0, 2000, 2000)),
        ):
            self.assertEqual(len(uia.list_elements(1, (0, 0, 1000, 1000), None)), 70)


if __name__ == "__main__":
    unittest.main()
