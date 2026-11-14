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


def mk_prompt(pid="p", text="a {emotion} cat", vars_=None, questions=("Is the cat {emotion}?",), **kw):
    p = {"id": pid, "prompt": text, "vars": vars_ if vars_ is not None else {"emotion": {"laughing": "laughing hard"}}, **kw}
    if questions is not None:
        p["judge"] = list(questions)
    p["_variants"] = bench.expand_variants(p, {})
    p["_mode"] = bench.prompt_mode(p)
    return p


def mk_gen(gid, p, variant=None, **kw):
    g = {"id": gid, "status": "ok", "mode": "txt2img", "model": "m", "prompt": p["id"] if isinstance(p, dict) else p,
         "variant": variant if variant is not None else {"emotion": "laughing"}, "files": [f"m/{gid}.png"]}
    if isinstance(p, dict):
        g["prompt_rev"] = bench.prompt_rev(p)
    g.update(kw)
    return g


JCFG = {"url": "http://x/v1", "model": "vl", "quality": ["Clean?"]}


class T_core(unittest.TestCase):
    def test_render_uses_value_not_label(self):
        v = {"emotion": {"label": "laughing", "value": "laughing heartily"}}
        self.assertEqual(judge.render_questions(["Is she {emotion}?"], v), ["Is she laughing heartily?"])

    def test_qhash_stable_and_sensitive(self):
        h = judge.qhash("vl", ["A?", "B?"], ["Q?"])
        self.assertEqual(h, judge.qhash("vl", ["A?", "B?"], ["Q?"]))
        self.assertEqual(len(h), 12)
        self.assertNotEqual(h, judge.qhash("vl", ["A?", "C?"], ["Q?"]))
        self.assertNotEqual(h, judge.qhash("other", ["A?", "B?"], ["Q?"]))
        self.assertNotEqual(h, judge.qhash("vl", ["A?"], ["B?", "Q?"]))

    def test_parse_answers_plain(self):
        self.assertEqual(judge.parse_answers('{"1": "yes", "2": "NO"}', 2), ["yes", "no"])

    def test_parse_answers_fenced(self):
        self.assertEqual(judge.parse_answers('```json\n{"1":"Yes","2":"no"}\n```', 2), ["yes", "no"])

    def test_parse_answers_prose_around(self):
        self.assertEqual(judge.parse_answers('Sure: {"1": "no"} done', 1), ["no"])

    def test_parse_answers_missing_number(self):
        with self.assertRaises(ValueError):
            judge.parse_answers('{"1": "yes"}', 2)

    def test_parse_answers_bad_value(self):
        for txt in ('{"1": "Yes."}', '{"1": true}', '{"1": "maybe"}', "no json", '{"1": "yes"'):
            with self.assertRaises(ValueError, msg=txt):
                judge.parse_answers(txt, 1)

    def test_parse_questions(self):
        self.assertEqual(judge.parse_questions('```json\n["A?", "B?"]\n```'), ["A?", "B?"])
        with self.assertRaises(ValueError):
            judge.parse_questions("no list")
        with self.assertRaises(ValueError):
            judge.parse_questions('["A?", 3]')

    def test_score(self):
        self.assertEqual(judge.score([["a", "yes"], ["b", "no"]]), 0.5)
        self.assertIsNone(judge.score([]))


class T_select(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cb-judge-"))
        (self.tmp / "m").mkdir()
        self.p = mk_prompt()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch(self, *gids):
        for gid in gids:
            (self.tmp / "m" / f"{gid}.png").write_bytes(b"png")

    def select(self, gens, plist=None, judgments=None, **opts):
        return judge.select(gens, plist or [self.p], JCFG, self.tmp, judgments or {}, opts)

    def test_select_reasons(self):
        p2 = mk_prompt("noq", questions=None)
        self.touch("ok", "noprompt", "noq", "old", "cur", "imp", "err")
        gens = [mk_gen("ok", self.p), mk_gen("noprompt", "gone"), mk_gen("noq", p2),
                mk_gen("old", self.p, prompt_rev="000000000000"), mk_gen("missing", self.p),
                mk_gen("cur", self.p), mk_gen("imp", self.p, mode="import"), mk_gen("err", self.p, status="error")]
        _, info = judge.expected(gens[5], {"p": self.p}, JCFG, self.tmp)
        todo, skipped = self.select(gens, [self.p, p2], {"cur": {"qhash": info["qhash"]}})
        self.assertEqual([g["id"] for g, _ in todo], ["ok"])
        self.assertEqual(dict(skipped), {"no-prompt": 1, "no-questions": 1, "old-wording": 1,
                                         "missing-file": 1, "current": 1})
        self.assertEqual(todo[0][1]["fidelity"], ["Is the cat laughing hard?"])
        self.assertEqual(todo[0][1]["quality"], ["Clean?"])

    def test_select_unknown_variant(self):
        self.touch("a")
        todo, skipped = self.select([mk_gen("a", self.p, variant={"emotion": "sad"})])
        self.assertEqual(todo, [])
        self.assertEqual(dict(skipped), {"unknown-variant": 1})

    def test_select_ignores_sweep_axes(self):
        self.touch("a")
        todo, _ = self.select([mk_gen("a", self.p, variant={"emotion": "laughing", "cfg": "5"})])
        self.assertEqual(len(todo), 1)

    def test_select_force_and_ids(self):
        self.touch("a", "b")
        gens = [mk_gen("a", self.p), mk_gen("b", self.p)]
        _, info = judge.expected(gens[0], {"p": self.p}, JCFG, self.tmp)
        cur = {"a": {"qhash": info["qhash"]}}
        self.assertEqual([g["id"] for g, _ in self.select(gens, judgments=cur)[0]], ["b"])
        self.assertEqual([g["id"] for g, _ in self.select(gens, judgments=cur, force=True)[0]], ["a", "b"])
        self.assertEqual([g["id"] for g, _ in self.select(gens, ids={"a"}, force=True)[0]], ["a"])

    def test_select_filters(self):
        self.touch("a")
        g = mk_gen("a", self.p)
        self.assertEqual(len(self.select([g], models=["other"])[0]), 0)
        self.assertEqual(len(self.select([g], prompts=["other"])[0]), 0)
        self.assertEqual(len(self.select([g], categories=["Text"])[0]), 0)
        self.assertEqual(len(self.select([g], models=["m"], prompts=["p"])[0]), 1)

    def test_select_legacy_without_rev(self):
        self.touch("a")
        g = mk_gen("a", self.p)
        del g["prompt_rev"]
        self.assertEqual(len(self.select([g])[0]), 1)

    def test_no_quality_questions(self):
        self.touch("a")
        todo, _ = judge.select([mk_gen("a", self.p)], [self.p], {"url": "u", "model": "vl"}, self.tmp, {}, {})
        self.assertEqual(todo[0][1]["quality"], [])


if __name__ == "__main__":
    unittest.main()
