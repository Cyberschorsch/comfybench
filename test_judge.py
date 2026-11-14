"""Unit tests for judge.py – run with: python3 -m unittest -v"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.pop("COMFYBENCH_HOME", None)  # before importing bench: ROOT = repo dir

import bench
import judge
from bench import BenchError


class T_check_questions(unittest.TestCase):
    P = {"id": "p", "vars": {"emotion": ["a"]}}

    def test_check_questions_ok(self):
        judge.check_questions(["Is she {emotion}?", "Is it a photo?"], self.P)

    def test_check_questions_unknown_placeholder(self):
        with self.assertRaises(BenchError):
            judge.check_questions(["Is it {color}?"], self.P)

    def test_check_questions_empty_or_non_string(self):
        for qs in ([], [""], ["  "], [3], "x", None):
            with self.assertRaises(BenchError, msg=repr(qs)):
                judge.check_questions(qs, self.P)


class T_validate(unittest.TestCase):
    def test_validate_config_judge(self):
        with self.assertRaises(BenchError):
            bench.validate_config({"models": {}, "judge": {"url": "", "model": "m"}})
        with self.assertRaises(BenchError):
            bench.validate_config({"models": {}, "judge": {"url": "http://x/v1", "model": ""}})
        with self.assertRaises(BenchError):
            bench.validate_config({"models": {}, "judge": {"url": "http://x/v1", "model": "m", "quality": "x"}})
        bench.validate_config({"models": {}, "judge": {"url": "http://x/v1", "model": "m", "quality": ["Q?"]}})

    def test_validate_prompts_judge(self):
        doc = {"prompts": [{"id": "p", "prompt": "a {emotion} cat", "vars": {"emotion": ["sad"]},
                            "judge": ["Is it {nope}?"]}]}
        with self.assertRaises(BenchError):
            bench.validate_prompts(doc)
        doc["prompts"][0]["judge"] = ["Is the cat {emotion}?"]
        bench.validate_prompts(doc)

    def test_prompt_key_order(self):
        order = bench.PROMPT_KEY_ORDER
        self.assertEqual(order.index("judge"), order.index("check") + 1)


class T_store_judgments(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cb-judge-"))
        self.store = bench.Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_store_judgments_roundtrip(self):
        self.assertEqual(self.store.judgments(), {})
        entry = {"qhash": "abc", "fidelity_score": 0.5}
        self.store.set_judgment("a", entry)
        self.assertEqual(self.store.judgments()["a"], entry)
        self.assertEqual(json.loads((self.tmp / "judge.json").read_text(encoding="utf-8"))["a"], entry)

    def test_delete_removes_judgment(self):
        self.store.append({"id": "a", "status": "ok", "files": []})
        self.store.set_judgment("a", {"qhash": "abc"})
        self.store.delete_generation("a")
        self.assertNotIn("a", self.store.judgments())


if __name__ == "__main__":
    unittest.main()
