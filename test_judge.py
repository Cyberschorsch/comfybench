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


class FakeResp:
    def __init__(self, status=200, content="", body=None):
        self.status_code = status
        self._body = body if body is not None else {"choices": [{"message": {"content": content}}]}
        self.text = body if isinstance(body, str) else json.dumps(self._body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class FakePost:
    """Replaces judge.requests.post; replies are FakeResp objects or exceptions, used in order."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}, "timeout": timeout})
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


class PatchPost(unittest.TestCase):
    def patch(self, *replies):
        self.fake = FakePost(*replies)
        orig = judge.requests.post
        judge.requests.post = self.fake
        self.addCleanup(setattr, judge.requests, "post", orig)
        return self.fake


class T_http(PatchPost):
    def setUp(self):
        self._key = os.environ.pop(judge.KEY_ENV, None)

    def tearDown(self):
        os.environ.pop(judge.KEY_ENV, None)
        if self._key is not None:
            os.environ[judge.KEY_ENV] = self._key

    def test_chat_payload(self):
        fake = self.patch(FakeResp(content="hi"), FakeResp(content="ho"))
        self.assertEqual(judge.chat({"url": "http://h/v1/", "model": "vl"}, "q"), "hi")
        c = fake.calls[0]
        self.assertEqual(c["url"], "http://h/v1/chat/completions")
        self.assertEqual(c["json"]["temperature"], 0)
        self.assertEqual(c["json"]["model"], "vl")
        self.assertEqual(c["json"]["messages"], [{"role": "user", "content": "q"}])
        self.assertEqual(c["timeout"], judge.TIMEOUT)
        self.assertNotIn("Authorization", c["headers"])
        os.environ[judge.KEY_ENV] = "k"
        judge.chat({"url": "http://h/v1", "model": "vl"}, "q")
        self.assertEqual(fake.calls[1]["headers"]["Authorization"], "Bearer k")

    def test_image_content_data_url(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.png"
            f.write_bytes(b"\x89PNGdata")
            content = judge.image_content(f, ["Q1", "Q2"])
        self.assertIn("1. Q1", content[0]["text"])
        self.assertIn("2. Q2", content[0]["text"])
        url = content[1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))
        import base64
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), b"\x89PNGdata")

    def test_questions_content(self):
        p = mk_prompt(check="Laugh believable?")
        text = judge.questions_content(p)
        self.assertIn("a {emotion} cat", text)
        self.assertIn("{emotion}", text)
        self.assertIn("Laugh believable?", text)

    def test_chat_abort_on_connection_error_and_4xx(self):
        cfg = {"url": "http://h/v1", "model": "vl"}
        self.patch(judge.requests.ConnectionError("refused"))
        with self.assertRaises(judge.JudgeAbort):
            judge.chat(cfg, "q")
        for code in (400, 401, 404):
            self.patch(FakeResp(code, body="model does not support images"))
            with self.assertRaises(judge.JudgeAbort) as cm:
                judge.chat(cfg, "q")
            self.assertIn("model does not support images", str(cm.exception))
            self.assertIn(str(code), str(cm.exception))

    def test_chat_runtime_on_5xx_and_timeout(self):
        cfg = {"url": "http://h/v1", "model": "vl"}
        for reply in (FakeResp(500, body="boom"), judge.requests.Timeout("slow"), FakeResp(200, body="<html>")):
            self.patch(reply)
            with self.assertRaises(RuntimeError):
                judge.chat(cfg, "q")

    def test_judge_abort_is_bench_error(self):
        self.assertTrue(issubclass(judge.JudgeAbort, BenchError))

    def test_retry(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("bad")
            return "ok"
        self.assertEqual(judge.retry(flaky), "ok")

        def broken():
            raise RuntimeError("down")
        with self.assertRaises(RuntimeError):
            judge.retry(broken)

    def test_retry_does_not_retry_abort(self):
        calls = []

        def abort():
            calls.append(1)
            raise judge.JudgeAbort("stop")
        with self.assertRaises(judge.JudgeAbort):
            judge.retry(abort)
        self.assertEqual(len(calls), 1)


PROMPTS_YAML = """# my hand-written comment
prompts:
  - id: cat
    prompt: a {emotion} cat
    vars:
      emotion: {laughing: laughing hard}
  - id: dog
    prompt: a dog
    judge: ["Is there a dog?"]
"""


class T_run(PatchPost):
    """judge.run against a temp data dir with a fake endpoint."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cb-judge-run-"))
        self._root = bench.ROOT
        bench.ROOT = self.tmp
        self.cfg_path, self.prompts_path = self.tmp / "config.yaml", self.tmp / "prompts.yaml"
        self.cfg_path.write_text("models: {}\njudge: {url: 'http://h/v1', model: vl, quality: ['Clean?']}\n",
                                 encoding="utf-8")
        self.prompts_path.write_text(PROMPTS_YAML, encoding="utf-8")
        self.store = bench.Store(self.tmp / "results")
        self.events = []

    def tearDown(self):
        bench.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def emit(self, ev, **d):
        self.events.append((ev, d))

    def add_gen(self, gid, prompt="dog", variant=None):
        cfg, prompts = bench.load_all(self.cfg_path, self.prompts_path)
        p = next(x for x in prompts if x["id"] == prompt)
        f = self.tmp / "results" / "m" / f"{gid}.png"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"png")
        self.store.append({"id": gid, "status": "ok", "mode": "txt2img", "model": "m", "prompt": prompt,
                           "variant": variant or {}, "prompt_rev": bench.prompt_rev(p), "files": [f"m/{gid}.png"]})

    def run_judge(self, **opts):
        cfg, prompts = bench.load_all(self.cfg_path, self.prompts_path)
        return judge.run(cfg, prompts, self.prompts_path, opts, self.emit)

    def test_questions_only_writes_once_with_backup(self):
        self.patch(FakeResp(content='["Is the cat {emotion}?", "Is it a cat?"]'))
        s = self.run_judge(questions_only=True)
        self.assertEqual(s["questions"], 1)
        self.assertEqual(len(self.fake.calls), 1)          # dog already has questions
        doc = bench.load_yaml(self.prompts_path)
        by = {p["id"]: p for p in doc["prompts"]}
        self.assertEqual(by["cat"]["judge"], ["Is the cat {emotion}?", "Is it a cat?"])
        self.assertEqual(by["dog"]["judge"], ["Is there a dog?"])
        self.assertEqual(len(list((self.tmp / ".backups").glob("prompts.*.yaml"))), 1)

    def test_questions_invalid_placeholder_is_retried_then_failed(self):
        self.patch(FakeResp(content='["Is it {color}?"]'), FakeResp(content='["Is it {color}?"]'))
        s = self.run_judge(questions_only=True)
        self.assertEqual((s["questions"], s["failed"]), (0, 1))
        self.assertEqual(self.prompts_path.read_text(encoding="utf-8"), PROMPTS_YAML)

    def test_judge_does_not_touch_prompts_yaml(self):
        self.add_gen("g1")
        self.patch(FakeResp(content='{"1": "yes", "2": "yes"}'))
        self.run_judge()
        self.assertEqual(self.prompts_path.read_text(encoding="utf-8"), PROMPTS_YAML)

    def test_judge_writes_entry_and_second_run_noop(self):
        self.add_gen("g1")
        self.patch(FakeResp(content='{"1": "no", "2": "yes"}'))
        s = self.run_judge()
        self.assertEqual(s["done"], 1)
        e = self.store.judgments()["g1"]
        self.assertEqual(e["fidelity"], [["Is there a dog?", "no"]])
        self.assertEqual(e["quality"], [["Clean?", "yes"]])
        self.assertEqual((e["fidelity_score"], e["quality_score"]), (0.0, 1.0))
        self.assertEqual(e["judge"], "vl")
        ok = [d for ev, d in self.events if ev == "ok"]
        self.assertEqual(ok[0]["note"], "fidelity 0/1 · quality 1/1")
        self.assertTrue((self.tmp / "results" / "index.html").is_file())
        s2 = self.run_judge()
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(s2["skipped_by"], {"current": 1})

    def test_judge_retry_then_fail_continues(self):
        self.add_gen("g1")
        self.add_gen("g2")
        self.patch(FakeResp(content="garbage"), FakeResp(content="garbage"),
                   FakeResp(content='{"1": "yes", "2": "yes"}'))
        s = self.run_judge()
        self.assertEqual((s["done"], s["failed"]), (1, 1))
        self.assertEqual(list(self.store.judgments()), ["g2"])

    def test_judge_abort_on_4xx(self):
        self.add_gen("g1")
        self.add_gen("g2")
        self.patch(FakeResp(404, body="model not found"))
        s = self.run_judge()
        self.assertTrue(s["stopped"])
        self.assertEqual(self.store.judgments(), {})
        self.assertIn("model not found", [d for ev, d in self.events if ev == "abort"][0]["msg"])

    def test_no_questions_warns(self):
        self.add_gen("c1", prompt="cat", variant={"emotion": "laughing"})
        self.patch()
        s = self.run_judge()
        self.assertEqual(s["skipped_by"], {"no-questions": 1})
        warns = " ".join(d["msg"] for ev, d in self.events if ev == "warn")
        self.assertIn("--questions-only", warns)

    def test_stop_before_next_item(self):
        self.add_gen("g1")
        import threading
        stop = threading.Event()
        stop.set()
        self.patch()
        cfg, prompts = bench.load_all(self.cfg_path, self.prompts_path)
        s = judge.run(cfg, prompts, self.prompts_path, {}, self.emit, stop)
        self.assertTrue(s["stopped"])
        self.assertEqual(self.fake.calls, [])

    def test_no_judge_config(self):
        self.cfg_path.write_text("models: {}\n", encoding="utf-8")
        with self.assertRaises(BenchError) as cm:
            self.run_judge()
        self.assertIn("judge:", str(cm.exception))


class T_cli(unittest.TestCase):
    def test_run_judge_flag_uses_ids(self):
        captured = {}
        orig_exec, orig_run, orig_load = bench.execute, judge.run, bench.load_all
        bench.execute = lambda cfg, prompts, opts, emit, *a, **k: {"new_ids": ["a"], "stopped": False, "done": 1}
        judge.run = lambda cfg, prompts, path, opts, emit, stop=None: captured.update(opts) or {}
        bench.load_all = lambda c, p: ({"judge": {"url": "u", "model": "m"}}, [])
        try:
            import argparse
            args = argparse.Namespace(config="c", prompts_file="p", models=None, prompts=None, category=None,
                                      var=None, sweep=None, lora_combo=None, sweep_loras=None, force=False,
                                      dry_run=False, judge=True)
            bench.cmd_run(args)
        finally:
            bench.execute, judge.run, bench.load_all = orig_exec, orig_run, orig_load
        self.assertEqual(captured["ids"], {"a"})


if __name__ == "__main__":
    unittest.main()
