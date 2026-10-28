"""Unit tests for bench.py – run with: python3 -m unittest -v"""
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

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


# ------------------------------------------------------------------ Civitai import

CIVITAI_ITEM = {
    "id": 12097475,
    "url": "https://image.civitai.com/xG1nkq/936f0e8d/original=true/936f0e8d.jpeg",
    "width": 1520, "height": 1952,
    "meta": {
        "Size": "896x1152", "seed": 3269595310, "steps": 25,
        "sampler": "Euler a", "cfgScale": 7, "Schedule type": "Karras",
        "prompt": "1girl, portrait, soft light", "negativePrompt": "lowres",
        "Model": "ponyDiffusionV6XL", "Denoising strength": 0.6,
    },
}

CIVITAI_COMFY_GRAPH = {"prompt": {
    "3": {"class_type": "KSampler", "inputs": {"positive": ["6", 0], "negative": ["7", 0],
                                               "seed": 777, "steps": 20, "cfg": 4.5,
                                               "sampler_name": "euler", "scheduler": "normal"}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "comfy positive"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "comfy negative"}},
    "8": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "modelX.safetensors"}},
}}


class T_parse_civitai_url(unittest.TestCase):
    def test_image_url(self):
        self.assertEqual(bench.parse_civitai_url("https://civitai.com/images/123"), ("civitai.com", "imageId", "123"))

    def test_post_url(self):
        self.assertEqual(bench.parse_civitai_url("https://civitai.com/posts/456"), ("civitai.com", "postId", "456"))

    def test_no_scheme_query_and_www(self):
        self.assertEqual(bench.parse_civitai_url("civitai.com/images/789?query=x#h"), ("civitai.com", "imageId", "789"))
        self.assertEqual(bench.parse_civitai_url("https://www.civitai.com/images/789"), ("civitai.com", "imageId", "789"))

    def test_civitai_red(self):
        self.assertEqual(bench.parse_civitai_url("https://civitai.red/images/123"), ("civitai.red", "imageId", "123"))
        self.assertEqual(bench.parse_civitai_url("www.civitai.red/posts/45"), ("civitai.red", "postId", "45"))

    def test_rejects_other_sites(self):
        for url in ("https://example.com/images/1", "https://image.civitai.com/x/abc.jpeg",
                    "https://image.civitai.red/x/abc.jpeg", "https://civitai.example.com/images/1",
                    "https://civitai.blue/images/1"):
            with self.assertRaises(BenchError, msg=url):
                bench.parse_civitai_url(url)

    def test_rejects_other_civitai_pages(self):
        for url in ("https://civitai.com/models/123", "https://civitai.com/images/",
                    "https://civitai.com/", "https://civitai.com/posts/abc"):
            with self.assertRaises(BenchError, msg=url):
                bench.parse_civitai_url(url)


class T_civitai_extract(unittest.TestCase):
    def test_flat_a1111_meta(self):
        pos, neg, seed, model, params, size = bench.civitai_extract(CIVITAI_ITEM)
        self.assertEqual(pos, "1girl, portrait, soft light")
        self.assertEqual(neg, "lowres")
        self.assertEqual(seed, 3269595310)
        self.assertEqual(model, "ponyDiffusionV6XL")
        self.assertEqual(params, {"steps": 25, "cfg": 7, "sampler": "euler_ancestral",
                                  "scheduler": "karras", "denoise": 0.6})   # A1111 names → ComfyUI
        self.assertEqual(size, (896, 1152))   # generation size wins over the file's 1520x1952

    def test_comfy_graph_fills_gaps(self):
        item = {"id": 1, "url": "https://image.civitai.com/a/original=true/b.png", "width": 1024, "height": 1024,
                "meta": {"id": 1, "meta": {"seed": 1, "comfy": json.dumps(CIVITAI_COMFY_GRAPH)}}}
        pos, neg, seed, model, params, size = bench.civitai_extract(item)
        self.assertEqual(pos, "comfy positive")          # nested {id, meta} shape unwrapped
        self.assertEqual(neg, "comfy negative")
        self.assertEqual(seed, 777)
        self.assertEqual(model, "modelX.safetensors")
        self.assertEqual(params, {"steps": 20, "cfg": 4.5, "sampler": "euler", "scheduler": "normal"})
        self.assertEqual(size, (1024, 1024))

    def test_comfy_wins_over_flat(self):
        item = {"id": 1, "url": "u", "width": 1, "height": 1,
                "meta": {"prompt": "flat prompt", "steps": 30,
                         "comfy": json.dumps(CIVITAI_COMFY_GRAPH)}}
        pos, _, seed, _, params, _ = bench.civitai_extract(item)
        self.assertEqual(pos, "comfy positive")     # the graph is ground truth
        self.assertEqual(params["steps"], 20)       # …flat keys only fill its gaps
        self.assertEqual(seed, 777)

    def test_broken_comfy_json_falls_back(self):
        item = {"id": 1, "url": "u", "width": 1, "height": 1,
                "meta": {"prompt": "flat prompt", "comfy": "{not json"}}
        pos, *_ = bench.civitai_extract(item)
        self.assertEqual(pos, "flat prompt")

    def test_sampler_names_to_comfy(self):
        cases = {
            ("Euler a", None): ("euler_ancestral", None),
            ("DPM++ 2M Karras", None): ("dpmpp_2m", "karras"),           # legacy combined name
            ("DPM++ 2M SDE", "Exponential"): ("dpmpp_2m_sde", "exponential"),
            ("DPM++ 2M Karras", "SGM Uniform"): ("dpmpp_2m", "sgm_uniform"),  # explicit schedule type wins
            ("dpmpp_2m", "karras"): ("dpmpp_2m", "karras"),             # already ComfyUI names
            ("GPT-Image-2.5-flare", None): (None, None),                 # model name from Civitai's generator
            ("Restart", "Align Your Steps"): (None, None),               # no ComfyUI equivalent
            (None, "Automatic"): (None, None),
            # spellings seen live on Civitai
            ("euler_simple", None): ("euler", "simple"),
            ("Euler simple", None): ("euler", "simple"),
            ("euler_a", "simple"): ("euler_ancestral", "simple"),
            ("ER SDE", "Beta"): ("er_sde", "beta"),
            ("DPM++ 2M SDE Heun Karras", None): ("dpmpp_2m_sde_heun", "karras"),
            ("Undefined", None): (None, None),
            ("linear/euler", None): (None, None),
            ("res_2s", None): (None, None),                              # custom node sampler, not core ComfyUI
        }
        for (s, sch), want in cases.items():
            self.assertEqual(bench.civitai_sampler(s, sch), want, (s, sch))

    def test_generator_placeholders_are_dropped(self):
        # Civitai's on-site generator with API models (e.g. OpenAI) sends seed -1, steps 0, cfgScale 0
        item = {"id": 1, "url": "u", "width": 1024, "height": 1536,
                "meta": {"prompt": "p", "seed": -1, "steps": 0, "cfgScale": 0}}
        _, _, seed, _, params, _ = bench.civitai_extract(item)
        self.assertIsNone(seed)
        self.assertNotIn("steps", params)
        self.assertNotIn("cfg", params)

    def test_no_prompt(self):
        pos, neg, seed, model, params, size = bench.civitai_extract({"id": 1, "url": "u", "meta": {}})
        self.assertIsNone(pos)
        self.assertEqual(params, {})


class T_import_civitai(unittest.TestCase):
    """End-to-end: URL in → gallery record + prompts.yaml entry, against a temp data dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cb-civitai-"))
        (self.tmp / "config.yaml").write_text("models: {}\n", encoding="utf-8")
        (self.tmp / "prompts.yaml").write_text("prompts: []\n", encoding="utf-8")
        self.item = dict(CIVITAI_ITEM)   # per-test copy, so tests cannot leak into each other
        self.image_downloads = 0
        self.api_urls = []
        self._root, self._get = bench.ROOT, bench.requests.get
        bench.ROOT = self.tmp
        bench.requests.get = self.fake_get

    def tearDown(self):
        bench.ROOT, bench.requests.get = self._root, self._get
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_get(self, url, params=None, timeout=None):
        item = self.item
        if url.startswith("https://image.civitai.com/"):
            self.image_downloads += 1
        class R:
            status_code = 200
            headers = {}
            content = b"\x89PNG\r\n\x1a\nfakepng"

            def json(self):
                return {"items": [item]}
        if re.match(r"^https://civitai\.(com|red)/api/v1/images$", url):
            self.api_urls.append(url)
            assert params == {"imageId": "12097475", "withMeta": "true", "nsfw": "X"}, params
        elif not url.startswith("https://image.civitai.com/"):
            raise AssertionError(f"unexpected request: {url}")
        return R()

    def run_import(self, url="https://civitai.com/images/12097475"):
        return bench.import_civitai(url, self.tmp / "config.yaml", self.tmp / "prompts.yaml")

    def prompts_doc(self):
        return bench.load_yaml(self.tmp / "prompts.yaml")

    def test_civitai_red_uses_its_own_host(self):
        res = self.run_import("https://civitai.red/images/12097475")
        self.assertEqual(self.api_urls, ["https://civitai.red/api/v1/images"])
        self.assertEqual(res["url"], "https://civitai.red/images/12097475")
        g, = bench.Store(self.tmp / "results").generations()
        self.assertEqual(g["source"], "https://civitai.red/images/12097475")
        self.assertEqual(bench.civitai_preview("civitai.red/images/12097475")["url"],
                         "https://civitai.red/images/12097475")

    def test_same_image_on_both_hosts_is_one_import(self):
        self.run_import("https://civitai.com/images/12097475")
        res = self.run_import("https://civitai.red/images/12097475")   # same id, same picture
        self.assertFalse(res["image_added"])
        self.assertFalse(res["prompt_added"])
        self.assertEqual(self.image_downloads, 1)

    def test_first_import_adds_record_and_prompt(self):
        res = self.run_import()
        self.assertTrue(res["image_added"])
        self.assertTrue(res["prompt_added"])
        self.assertEqual(res["prompt_id"], "civitai-12097475")
        gens = bench.Store(self.tmp / "results").generations()
        self.assertEqual(len(gens), 1)
        g = gens[0]
        self.assertEqual(g["mode"], "import")
        self.assertEqual(g["fingerprint"], "civitai:12097475")
        self.assertEqual(g["seed"], 3269595310)
        self.assertEqual(g["text"], "1girl, portrait, soft light")
        self.assertEqual(g["negative"], "lowres")
        self.assertEqual(g["values"]["width"], 896)
        self.assertEqual(g["values"]["checkpoint"], "ponyDiffusionV6XL")
        self.assertEqual(g["source"], "https://civitai.com/images/12097475")
        self.assertTrue((self.tmp / "results" / g["files"][0]).is_file())   # files are relative to results/
        p = self.prompts_doc()["prompts"]
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]["id"], "civitai-12097475")
        self.assertEqual(p[0]["category"], "Civitai")
        self.assertEqual(p[0]["prompt"], "1girl, portrait, soft light")
        self.assertEqual(p[0]["negative"], "lowres")
        self.assertEqual(p[0]["seeds"], [3269595310])
        self.assertEqual(p[0]["width"], 896)
        self.assertEqual(p[0]["params"], {"steps": 25, "cfg": 7, "sampler": "euler_ancestral",
                                          "scheduler": "karras", "denoise": 0.6})

    def test_second_import_is_a_noop(self):
        self.run_import()
        self.assertEqual(self.image_downloads, 1)
        res = self.run_import()
        self.assertFalse(res["image_added"])
        self.assertFalse(res["prompt_added"])
        self.assertEqual(self.image_downloads, 1)   # a duplicate never re-downloads the picture
        self.assertEqual(len(bench.Store(self.tmp / "results").generations()), 1)
        self.assertEqual(len(self.prompts_doc()["prompts"]), 1)

    def test_existing_prompt_id_is_kept(self):
        (self.tmp / "prompts.yaml").write_text(
            "prompts:\n  - id: civitai-12097475\n    prompt: something else\n", encoding="utf-8")
        res = self.run_import()
        self.assertTrue(res["image_added"])
        self.assertFalse(res["prompt_added"])
        p = self.prompts_doc()["prompts"]
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]["prompt"], "something else")   # never overwritten

    def test_image_without_prompt_raises(self):
        self.item = dict(CIVITAI_ITEM, meta={"steps": 25})   # metadata shared, but no prompt text
        with self.assertRaises(BenchError):
            self.run_import()
        self.assertEqual(bench.Store(self.tmp / "results").generations(), [])
        self.assertEqual(self.prompts_doc(), {"prompts": []})

    def test_rejected_url_never_touches_the_network(self):
        def boom(url, params=None, timeout=None):
            raise AssertionError("network called")
        bench.requests.get = boom
        with self.assertRaises(BenchError):
            self.run_import("https://example.com/images/1")

    def test_preview_has_no_side_effects(self):
        res = bench.civitai_preview("https://civitai.com/images/12097475")
        self.assertEqual(res["prompt_id"], "civitai-12097475")
        self.assertEqual(res["url"], "https://civitai.com/images/12097475")
        self.assertEqual(res["prompt"], "1girl, portrait, soft light")
        self.assertEqual(res["negative"], "lowres")
        self.assertEqual(res["seed"], 3269595310)
        self.assertEqual(res["model"], "ponyDiffusionV6XL")
        self.assertEqual(res["params"], {"steps": 25, "cfg": 7, "sampler": "euler_ancestral",
                                         "scheduler": "karras", "denoise": 0.6})
        self.assertEqual((res["width"], res["height"]), (896, 1152))
        self.assertIn("image.civitai.com", res["thumb"])
        self.assertIn("width=300", res["thumb"])                     # small CDN preview variant, not the 1 MB file
        self.assertEqual(self.image_downloads, 0)                       # never fetched the file
        self.assertEqual(bench.Store(self.tmp / "results").generations(), [])   # never wrote
        self.assertEqual(self.prompts_doc()["prompts"], [])

    def test_preview_without_prompt_raises(self):
        self.item = dict(CIVITAI_ITEM, meta={"steps": 25})
        with self.assertRaises(BenchError):
            bench.civitai_preview("https://civitai.com/images/12097475")

    def test_custom_prompt_id_and_category(self):
        res = bench.import_civitai("https://civitai.com/images/12097475",
                                   self.tmp / "config.yaml", self.tmp / "prompts.yaml",
                                   prompt_id="my-grab", category="Favorites")
        self.assertEqual(res["prompt_id"], "my-grab")
        p = self.prompts_doc()["prompts"]
        self.assertEqual(p[0]["id"], "my-grab")
        self.assertEqual(p[0]["category"], "Favorites")
        gens = bench.Store(self.tmp / "results").generations()
        self.assertEqual(gens[0]["fingerprint"], "civitai:12097475")     # dedupe stays per image

    def test_invalid_custom_id_fails_before_anything(self):
        with self.assertRaises(BenchError):
            bench.import_civitai("https://civitai.com/images/12097475",
                                 self.tmp / "config.yaml", self.tmp / "prompts.yaml",
                                 prompt_id="Bad ID!")
        self.assertEqual(bench.Store(self.tmp / "results").generations(), [])
        self.assertEqual(self.prompts_doc()["prompts"], [])
        self.assertEqual(self.image_downloads, 0)

    def test_custom_id_on_reimport_adds_only_a_new_prompt(self):
        self.run_import()
        res = bench.import_civitai("https://civitai.com/images/12097475",
                                   self.tmp / "config.yaml", self.tmp / "prompts.yaml",
                                   prompt_id="second-name")
        self.assertFalse(res["image_added"])
        self.assertTrue(res["prompt_added"])
        self.assertEqual([p["id"] for p in self.prompts_doc()["prompts"]],
                         ["civitai-12097475", "second-name"])


if __name__ == "__main__":
    unittest.main()
