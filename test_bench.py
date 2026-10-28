"""Unit tests for bench.py – run with: python3 -m unittest -v"""
import os
import unittest

os.environ.pop("COMFYBENCH_HOME", None)  # before importing bench: ROOT = repo dir

import bench
from bench import BenchError

ROOT = bench.ROOT


class T_expand_variants(unittest.TestCase):
    def test_product(self):
        out = bench.expand_variants({"id": "p", "vars": {"a": [1, 2], "b": ["x", "y"]}}, {})
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0]["a"], {"label": "1", "value": 1})
        self.assertEqual(out[3]["b"], {"label": "y", "value": "y"})

    def test_zip(self):
        out = bench.expand_variants({"id": "p", "vary": "zip", "vars": {"a": [1, 2], "b": ["x", "y"]}}, {})
        self.assertEqual([(v["a"]["value"], v["b"]["value"]) for v in out], [(1, "x"), (2, "y")])

    def test_zip_unequal(self):
        with self.assertRaises(BenchError):
            bench.expand_variants({"id": "p", "vary": "zip", "vars": {"a": [1, 2, 3], "b": ["x", "y"]}}, {})

    def test_single(self):
        out = bench.expand_variants({"id": "p", "vary": "single", "vars": {"a": [1, 2, 3], "b": ["x", "y"]}}, {})
        self.assertEqual(len(out), 4)  # 1 base + 2 + 1
        self.assertEqual(out[0], {"a": {"label": "1", "value": 1}, "b": {"label": "x", "value": "x"}})
        self.assertEqual({v["a"]["value"] for v in out}, {1, 2, 3})
        self.assertEqual([v["b"]["value"] for v in out], ["x", "x", "x", "y"])

    def test_dollar_ref(self):
        shared = {"emotions": {"neutral": "calm", "laughing": "ha"}}
        out = bench.expand_variants({"id": "p", "vars": {"emotion": "$emotions"}}, shared)
        self.assertEqual([(v["emotion"]["label"], v["emotion"]["value"]) for v in out],
                         [("neutral", "calm"), ("laughing", "ha")])


class T_inject(unittest.TestCase):
    def test_alone_keeps_type(self):
        used = set()
        out = bench.inject({"a": "{{steps}}"}, {"steps": 30}, used)
        self.assertIs(out["a"], 30)
        self.assertEqual(used, {"steps"})

    def test_embedded_becomes_string(self):
        out = bench.inject({"a": "a {{x}} b"}, {"x": 5}, set())
        self.assertEqual(out["a"], "a 5 b")

    def test_missing_key_raises(self):
        with self.assertRaises(KeyError):
            bench.inject({"a": "{{nope}}"}, {}, set())


class T_prepare_workflow(unittest.TestCase):
    def setUp(self):
        self.template = {
            "3": {"class_type": "KSampler",
                  "inputs": {"seed": "{{seed}}", "positive": ["6", 0]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["7", 0]}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
        }
        self.values = {"prompt": "hello", "seed": 1, "model_id": "m1", "prompt_id": "p1"}

    def test_mapping_overwrites_link(self):
        wf = bench.prepare_workflow(self.template, {"prompt": ["6/text"]}, self.values, set())
        self.assertEqual(wf["6"]["inputs"]["text"], "hello")

    def test_mapping_missing_node(self):
        with self.assertRaises(BenchError):
            bench.prepare_workflow(self.template, {"prompt": ["99/text"]}, self.values, set())

    def test_saveimage_prefix(self):
        # prefix is only set when a mapping exists (empty mapping returns the injected template as-is)
        wf = bench.prepare_workflow(self.template, {"prompt": ["6/text"]}, self.values, set())
        self.assertEqual(wf["9"]["inputs"]["filename_prefix"], "comfybench/m1/p1")


class T_fingerprint(unittest.TestCase):
    def test_sdxl_fingerprint_is_stable(self):
        cfg, prompts = bench.load_all(ROOT / "config.yaml", ROOT / "prompts.yaml")
        model_id = "sdxl-base"
        model = cfg["models"][model_id]
        p = next(p for p in prompts if p["_mode"] == "txt2img")
        spec = bench.wf_spec(model.get("workflow"))
        template = bench.load_template(spec["file"])
        values = bench.build_values(cfg, model_id, model, p, p["_variants"][0], 42)
        wf = bench.prepare_workflow(template, spec["map"], values, set())
        fp = bench.fingerprint(wf)
        self.assertEqual(fp, bench.fingerprint(wf))
        # If this changes, every existing result is regenerated on the next run – only update on purpose.
        self.assertEqual(fp, "6e8a8afcedf7e1ec")


class T_find_text_target(unittest.TestCase):
    def test_follows_link_to_text(self):
        api = {
            "3": {"class_type": "KSampler", "inputs": {"positive": ["6", 0]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["7", 0]}},
            "7": {"class_type": "Primitive", "inputs": {"value": "hello"}},
        }
        self.assertEqual(bench.find_text_target(api, ["6", 0]), ("7", "value"))

    def test_switch_takes_active_branch(self):
        api = {
            "3": {"class_type": "KSampler", "inputs": {"positive": ["10", 0]}},
            "10": {"class_type": "IfElse", "inputs": {"switch": False,
                                                      "on_true": ["6", 0], "on_false": ["7", 0]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "true side"}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "false side"}},
        }
        self.assertEqual(bench.find_text_target(api, ["10", 0]), ("7", "text"))


class T_inject_lora_nodes(unittest.TestCase):
    def test_chains_lora_loaders(self):
        wf = {
            "18:1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
            "18:2": {"class_type": "KSampler", "inputs": {"model": ["18:1", 0]}},
        }
        combo = {"names": ["a", "b"], "strengths": {"a": 0.5},
                 "lora_info": {"a": {"name": "a.safetensors"}}}
        wf, ref = bench.inject_lora_nodes(wf, combo, ("18:2", "model"))
        self.assertEqual(wf["1"]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(wf["1"]["inputs"]["model"], ["18:1", 0])
        self.assertEqual(wf["1"]["inputs"]["strength_model"], 0.5)
        self.assertEqual(wf["1"]["inputs"]["lora_name"], "a.safetensors")
        self.assertEqual(wf["2"]["inputs"]["model"], ["1", 0])
        self.assertEqual(wf["18:2"]["inputs"]["model"], ["2", 0])
        self.assertEqual(ref, ("2", 0))


class T_load_template_guard(unittest.TestCase):
    def test_rejects_path_outside_root(self):
        with self.assertRaises(BenchError):
            bench.load_template("/etc/hostname")
        with self.assertRaises(BenchError):
            bench.load_template("../x.json")


if __name__ == "__main__":
    unittest.main()
