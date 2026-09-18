"""只验证离线字段传递；不判断源事实、提示词语义或生成效果。"""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_remix.cli import dispatch, parser
from video_remix.dreamina import Dreamina
from video_remix.project import add_asset, initialize, validate_spec
from video_remix.storage import atomic, read


class OfflineTransferTests(unittest.TestCase):
    def setUp(self):
        guards = ExitStack()
        self.addCleanup(guards.close)
        for boundary in ("subprocess.run", "subprocess.Popen", "os.system",
                         "socket.socket", "socket.create_connection", "socket.getaddrinfo",
                         "video_remix.dreamina.command"):
            guards.enter_context(patch(boundary, side_effect=AssertionError(
                "离线传递测试禁止外部调用：" + boundary)))
        for method in ("submit", "account", "preflight", "query", "download"):
            guards.enter_context(patch.object(Dreamina, method, side_effect=AssertionError(
                "离线传递测试禁止平台操作：" + method)))
        directory = guards.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(directory).resolve() / "中文 项目"
        initialize(self.root, "离线字段传递")
        # 本地字节夹具只用于登记、摘要和路径检查，不冒充真实图片验证。
        image = Path(directory) / "图片夹具.png"
        for asset in ("person", "scene", "product", "new-person", "new-scene"):
            image.write_bytes(("离线夹具：" + asset).encode("utf-8"))
            add_asset(self.root, asset, image, "素材库角色，不应覆盖本次生成角色")
        self.repeat = "抬起鞋尖→落下；完整重复同一动作，不用跳切替代。"

    def command(self, *arguments):
        return dispatch(parser().parse_args(["--project", str(self.root), *arguments]))

    def source_plan(self, provenance):
        plan = {
            "format": "video-remix-plan.v1", "id": "base", "kind": "replica",
            "provider": "dreamina", "model": "seedance2.0fast_vip", "mode": "image_text",
            "duration": 15, "ratio": "9:16", "resolution": "720p",
            "inputs": [{"type": "image", "asset": "person", "role": "成年女性人物参考\n自然表情"},
                       {"type": "image", "asset": "product", "role": "商品外观：奶油白鞋底；不改字样"},
                       {"type": "image", "asset": "scene", "role": "石板路场景参考；保持透视"}],
            "intent": "先展示脚部受力，再重复动作，最后转身。\n三次完整动作都保留。",
            "product": "粉色鞋面＋奶油白鞋底；侧标为 café / cafe\u0301。\n不改变外形。",
            "look": "成年女性站在石板路。\n手机自然光，保留轻微手抖。",
            "sound": '原句：“哇——你听，鞋底咚、咚！”\n轻吸气→连续快说；不在切镜处停顿。\n'
                     '保留 café / cafe\u0301、字面量 $HOME 和 `echo 原声`。\n',
            "constraints": "保持商品比例。\n不得增加字幕或省略重复动作。",
            "shots": [{"start": 0, "end": 5, "action": self.repeat,
                       "camera": "低机位，镜头停留。\n鞋尖始终入画。", "delivery": "轻吸气后连说。",
                       "sync": "第一次落地对应第一声‘咚’。"},
                      {"start": 5, "end": 10, "action": self.repeat,
                       "camera": "保持同一机位。", "delivery": "继续上一句，不重启语气。",
                       "sync": "第二次落地对应第二声‘咚’。"},
                      {"start": 10, "end": 15, "action": self.repeat,
                       "camera": "完成后缓慢抬镜。", "delivery": "句尾自然呼气。",
                       "sync": "第三次落地后才转身。"}],
        }
        if provenance:
            plan["provenance"] = {"source_to_output": [{"source": [0, 15], "output": [0, 15]}],
                                  "uncertain": ["仅供核对的来源记录：未测量光源Ω\n不可当生成指令。"]}
        return plan

    def control(self, roles):
        slots = {}
        for slot, before, after in (("person", "成年女性", "成年男性"),
                                    ("scene", "石板路", "木质露台")):
            image = {"from": slot, "to": "new-" + slot}
            if roles:
                image["role"] = after + "参考\n保持自然光与构图；不锁定第一帧。"
            slots[slot] = {"text": [{"path": "look", "from": before, "to": after}],
                           "images": [image]}
        return {"format": "video-remix-variation.v1", "id": "child", "parent": "base",
                "frozen_core": {"mechanism": "三次完整动作及连续原声保持。", "product_assets": ["product"]},
                "slots": slots}

    def assert_prompt_fields(self, prompt, plan):
        self.assertTrue(prompt.startswith("15 seconds; 9:16; image_text.\n\n"))
        role_block = "\n".join(f"@image{index}: {entry['role']}"
                               for index, entry in enumerate(plan["inputs"], 1))
        self.assertIn("INPUT ROLES\n" + role_block + "\n\n", prompt)
        for label, field in (("INTENT", "intent"), ("PRODUCT", "product"),
                             ("CAPTURE / LOOK", "look"), ("SOUND / DELIVERY", "sound")):
            self.assertIn((label + "\n" + plan[field] + "\n\n").encode("utf-8"),
                          prompt.encode("utf-8"))
        self.assertTrue(prompt.endswith("CONSTRAINTS\n" + plan["constraints"]))
        self.assertEqual(prompt.count(self.repeat), 3)
        for shot in plan["shots"]:
            self.assertIn(f"[{shot['start']}–{shot['end']}s] {shot['action']}", prompt)
            for field in ("camera", "delivery", "sync"):
                self.assertIn(field + ": " + shot[field], prompt)
        self.assertNotIn("provenance", prompt)
        self.assertNotIn("source_to_output", prompt)
        self.assertNotIn("仅供核对的来源记录", prompt)
        self.assertNotIn("素材库角色", prompt)

    def check_transfer(self, provenance, roles):
        source = self.source_plan(provenance)
        source_file = self.root / "source.json"
        atomic(source_file, source)
        compiled = self.command("compile", str(source_file))
        parent_files = [source_file, Path(compiled["plan"]), Path(compiled["spec"]),
                        self.root / "plans/base/spec.json"]
        snapshots = {path: path.read_bytes() for path in parent_files}
        self.assertEqual(read(compiled["plan"]), source)
        self.assert_prompt_fields(read(compiled["spec"])["prompt"], source)
        control = self.control(roles)
        control_file = self.root / "control.json"
        atomic(control_file, control)
        varied = self.command("vary", str(control_file))
        expected = deepcopy(source)
        expected.update(id="child", kind="variation", parent="base", change="受控替换：person, scene",
                        look="成年男性站在木质露台。\n手机自然光，保留轻微手抖。")
        for index, slot in ((0, "person"), (2, "scene")):
            replacement = control["slots"][slot]["images"][0]
            expected["inputs"][index]["asset"] = replacement["to"]
            if roles:
                expected["inputs"][index]["role"] = replacement["role"]
        self.assertEqual(read(varied["plan"]), expected)
        spec = read(varied["spec"])
        self.assertEqual(read(self.root / "plans/child/spec.json"), spec)
        self.assertEqual(spec["inputs"], expected["inputs"])
        self.assert_prompt_fields(spec["prompt"], expected)
        self.assert_preview_and_arguments(spec, expected)
        for path, before in snapshots.items():
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.rglob("run.json")), [])

    def assert_preview_and_arguments(self, spec, expected):
        preview = self.command("run", "child", "--budget", "100", "--estimate-per-job", "42")
        self.assertFalse(preview["execute"])
        summary, = preview["variants"]
        self.assertIsNone(summary["existing_task"])
        self.assertEqual(summary["inputs"], expected["inputs"])
        self.assertEqual(summary["prompt"].encode("utf-8"), spec["prompt"].encode("utf-8"))
        with patch("video_remix.dreamina.shutil.which", return_value="/offline/dreamina"):
            provider = Dreamina()
        assets = validate_spec(spec, self.root)
        arguments = provider.arguments(spec, self.root, assets)
        self.assertEqual(arguments[:2], ["/offline/dreamina", "multimodal2video"])
        expected_images = []
        for entry in expected["inputs"]:
            expected_images.extend(["--image", str(self.root / assets[entry["asset"]]["path"])])
        self.assertEqual(arguments[2:arguments.index("--prompt")], expected_images)
        prompt = arguments[arguments.index("--prompt") + 1]
        self.assertEqual(prompt.encode("utf-8"), summary["prompt"].encode("utf-8"))
        self.assert_prompt_fields(prompt, expected)
        self.assertEqual(arguments[arguments.index("--duration"):],
                         ["--duration", "15", "--ratio", "9:16", "--video_resolution", "720p",
                          "--model_version", "seedance2.0fast_vip", "--poll", "0"])

    def test_compile_vary_preview_and_arguments_preserve_payload(self):
        self.check_transfer(provenance=True, roles=True)

    def test_legacy_plan_and_image_replacements_remain_compatible(self):
        self.check_transfer(provenance=False, roles=False)


if __name__ == "__main__":
    unittest.main()
