"""Unit tests for judge.py – run with: python3 -m unittest -v"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
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

    def test_check_questions_label_suffix(self):
        judge.check_questions(["Is she {emotion:label}?", "Is she {emotion}?", "Is it a photo?"], self.P)

    def test_check_questions_unknown_placeholder_with_suffix(self):
        with self.assertRaises(BenchError):
            judge.check_questions(["Is it {color:label}?"], self.P)

    def test_check_questions_only_label_suffix_allowed(self):
        with self.assertRaises(BenchError):
            judge.check_questions(["Is it {emotion:value}?"], self.P)


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
        self.store.append({"id": "a", "status": "ok", "files": []})
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
        todo, skipped = self.select(gens, [self.p, p2], {"cur": {"qhash": info["qhash"], "description": "x"}})
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
        cur = {"a": {"qhash": info["qhash"], "description": "x"}}
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
    def __init__(self, status=200, content="", body=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
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

    def test_text_vars_sees_prompt_and_negative(self):
        self.assertEqual(judge.text_vars({"prompt": "a {emotion} cat", "vars": {"emotion": ["x"]}}), {"emotion"})
        self.assertEqual(judge.text_vars({"prompt": "a cat {emotion:label}", "vars": {"emotion": ["x"]}}), {"emotion"})
        self.assertEqual(judge.text_vars({"prompt": "a cat", "negative": "no {emotion}",
                                          "vars": {"emotion": ["x"]}}), {"emotion"})

    def test_text_vars_skips_workflow_values(self):
        self.assertEqual(judge.text_vars({"prompt": "a cat", "vars": {"denoise": [0.5, 0.7]}}), set())

    def test_questions_content(self):
        p = mk_prompt(check="Laugh believable?")
        text = judge.questions_content(p)
        self.assertIn("a {emotion} cat", text)
        self.assertIn("{emotion}", text)
        self.assertIn("Laugh believable?", text)

    def test_questions_content_shows_labels_values_and_prefix(self):
        p = mk_prompt()
        text = judge.questions_content(p)
        self.assertIn("{emotion} = laughing (laughing hard)", text)   # the model must see what gets filled in
        self.assertIn("{emotion:label}", text)

    def test_questions_content_shows_placeholder_in_the_examples(self):
        # The model imitates the instruction. Without a requirement per variable name it writes the
        # value literally ("Is she laughing?") and the list fails the check against text_vars.
        text = judge.questions_content(mk_prompt())
        self.assertIn("Use the placeholders literally: {emotion}", text)
        self.assertIn("At least one question must contain {emotion}.", text)
        self.assertIn("Never replace a placeholder by its value", text)
        self.assertIn('["Is there a cat?", "Is the cat {emotion}?"]', text)   # reply example

    def test_questions_content_needs_no_placeholder_for_workflow_variables(self):
        # denoise is not varied by the prompt text, so no question may be forced to use it
        text = judge.questions_content(mk_prompt(text="a cat", vars_={"denoise": [0.5, 0.7]}))
        self.assertNotIn("At least one question must contain {denoise}", text)
        self.assertNotIn("Use the placeholders literally", text)
        self.assertIn('["Is there a cat?", "Is the cat orange?"]', text)

    def test_questions_content_keeps_plain_example_without_variables(self):
        text = judge.questions_content(mk_prompt(text="a cat", vars_={}))
        self.assertIn('["Is there a cat?", "Is the cat orange?"]', text)

    def test_questions_content_caps_long_value_lists(self):
        p = mk_prompt(vars_={"emotion": {str(i): f"value {i}" for i in range(20)}})
        text = judge.questions_content(p)
        self.assertIn("(value 5)", text)
        self.assertNotIn("(value 6)", text)

    def test_questions_content_omits_repeated_label(self):
        p = mk_prompt(vars_={"age": [18, 30]})
        self.assertIn("{age} = 18, 30", judge.questions_content(p))

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


class RunBase(PatchPost):
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

class T_run(RunBase):
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

    def test_questions_ignoring_the_variable_are_asked_again(self):
        # cat's text varies by {emotion} – a list without it is asked again
        self.patch(FakeResp(content='["Is it a cat?"]'),
                   FakeResp(content='["Is the cat {emotion:label}?", "Is it a cat?"]'))
        s = self.run_judge(questions_only=True)
        self.assertEqual((s["questions"], s["failed"]), (1, 0))
        by = {p["id"]: p for p in bench.load_yaml(self.prompts_path)["prompts"]}
        self.assertEqual(by["cat"]["judge"], ["Is the cat {emotion:label}?", "Is it a cat?"])
        self.assertEqual(len(self.fake.calls), 2)

    def test_questions_ignoring_the_variable_fail_after_retry(self):
        self.patch(FakeResp(content='["Is it a cat?"]'), FakeResp(content='["Is it a cat?"]'))
        s = self.run_judge(questions_only=True)
        self.assertEqual((s["questions"], s["failed"]), (0, 1))
        self.assertIn("emotion", [d.get("msg", "") for ev, d in self.events if ev == "fail"][0])
        self.assertEqual(self.prompts_path.read_text(encoding="utf-8"), PROMPTS_YAML)

    def test_judge_does_not_touch_prompts_yaml(self):
        self.add_gen("g1")
        self.patch(FakeResp(content='{"1": "yes", "2": "yes"}'), FakeResp(content="A dog on grass."))
        self.run_judge()
        self.assertEqual(self.prompts_path.read_text(encoding="utf-8"), PROMPTS_YAML)

    def test_judge_writes_entry_and_second_run_noop(self):
        self.add_gen("g1")
        self.patch(FakeResp(content='{"1": "no", "2": "yes"}'), FakeResp(content="<think>hm</think> A dog on grass. "))
        s = self.run_judge()
        self.assertEqual(s["done"], 1)
        e = self.store.judgments()["g1"]
        self.assertEqual(e["fidelity"], [["Is there a dog?", "no"]])
        self.assertEqual(e["quality"], [["Clean?", "yes"]])
        self.assertEqual((e["fidelity_score"], e["quality_score"]), (0.0, 1.0))
        self.assertEqual(e["judge"], "vl")
        self.assertEqual(e["description"], "A dog on grass.")
        ok = [d for ev, d in self.events if ev == "ok"]
        self.assertEqual(ok[0]["note"], "fidelity 0/1 · quality 1/1")
        self.assertTrue((self.tmp / "results" / "index.html").is_file())
        s2 = self.run_judge()
        self.assertEqual(len(self.fake.calls), 2)
        self.assertEqual(s2["skipped_by"], {"current": 1})

    def test_judge_retry_then_fail_continues(self):
        self.add_gen("g1")
        self.add_gen("g2")
        self.patch(FakeResp(content="garbage"), FakeResp(content="garbage"),
                   FakeResp(content='{"1": "yes", "2": "yes"}'), FakeResp(content="A dog on grass."))
        s = self.run_judge()
        self.assertEqual((s["done"], s["failed"]), (1, 1))
        self.assertEqual(list(self.store.judgments()), ["g2"])

    def test_description_is_asked_after_the_answers(self):
        self.add_gen("g1")
        self.patch(FakeResp(content='{"1": "yes", "2": "yes"}'), FakeResp(content="  "), FakeResp(content="A dog."))
        s = self.run_judge()
        self.assertEqual(s["done"], 1)
        self.assertEqual(self.store.judgments()["g1"]["description"], "A dog.")   # empty reply retried
        texts = [c["json"]["messages"][0]["content"][0]["text"] for c in self.fake.calls]
        self.assertTrue(texts[0].startswith(judge.IMAGE_INSTRUCTION))
        self.assertEqual(texts[1:], [judge.DESCRIBE_INSTRUCTION] * 2)
        self.assertTrue(self.fake.calls[1]["json"]["messages"][0]["content"][1]["image_url"]["url"]
                        .startswith("data:image/png;base64,"))

    def test_description_failure_fails_the_item(self):
        self.add_gen("g1")
        self.patch(FakeResp(content='{"1": "yes", "2": "yes"}'), FakeResp(content=""), FakeResp(content=""))
        s = self.run_judge()
        self.assertEqual((s["done"], s["failed"]), (0, 1))
        self.assertEqual(self.store.judgments(), {})

    def test_entry_without_description_is_judged_again(self):
        self.add_gen("g1")
        self.patch(FakeResp(content='{"1": "yes", "2": "yes"}'), FakeResp(content="A dog."))
        self.run_judge()
        e = self.store.judgments()["g1"]
        del e["description"]
        self.store.set_judgment("g1", e)
        self.patch(FakeResp(content='{"1": "yes", "2": "yes"}'), FakeResp(content="A dog again."))
        s = self.run_judge()
        self.assertEqual(s["done"], 1)
        self.assertEqual(self.store.judgments()["g1"]["description"], "A dog again.")

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
            with contextlib.redirect_stdout(io.StringIO()):
                bench.cmd_run(args)
        finally:
            bench.execute, judge.run, bench.load_all = orig_exec, orig_run, orig_load
        self.assertEqual(captured["ids"], {"a"})


class T_review_fixes(RunBase):
    def test_image_deleted_before_its_turn_fails_only_that_item(self):
        self.add_gen("g1")
        self.add_gen("g2")
        orig = judge.select

        def select_then_delete(*a, **k):
            todo, skipped = orig(*a, **k)
            (self.tmp / "results" / "m" / "g1.png").unlink()
            return todo, skipped
        judge.select = select_then_delete
        self.addCleanup(setattr, judge, "select", orig)
        self.patch(FakeResp(content='{"1": "yes", "2": "yes"}'), FakeResp(content="A dog on grass."))
        s = self.run_judge()
        self.assertEqual((s["done"], s["failed"], s["stopped"]), (1, 1, False))
        self.assertEqual(list(self.store.judgments()), ["g2"])

    def test_set_judgment_skips_deleted_generation(self):
        self.store.append({"id": "a", "status": "ok", "files": []})
        self.assertEqual(self.store.set_judgment("a", {"qhash": "x"}), True)
        self.store.delete_generation("a")
        self.assertEqual(self.store.set_judgment("a", {"qhash": "x"}), False)
        self.assertEqual(self.store.judgments(), {})

    def test_questions_written_before_abort_are_kept(self):
        self.prompts_path.write_text(PROMPTS_YAML.replace('    judge: ["Is there a dog?"]\n', ""), encoding="utf-8")
        self.patch(FakeResp(content='["Is the cat {emotion}?"]'), KeyError("unexpected"))
        with self.assertRaises(KeyError):
            self.run_judge(questions_only=True)
        by = {p["id"]: p for p in bench.load_yaml(self.prompts_path)["prompts"]}
        self.assertEqual(by["cat"]["judge"], ["Is the cat {emotion}?"])
        self.assertNotIn("judge", by["dog"])

    def test_other_request_errors(self):
        cfg = {"url": "http://h/v1", "model": "vl"}
        self.patch(judge.requests.exceptions.ChunkedEncodingError("cut"))
        with self.assertRaises(RuntimeError):
            judge.chat(cfg, "q")
        for exc in (judge.requests.exceptions.MissingSchema("no scheme"), judge.requests.exceptions.InvalidURL("bad")):
            self.patch(exc)
            with self.assertRaises(judge.JudgeAbort):
                judge.chat(cfg, "q")

    def test_rate_limit_and_request_timeout_are_retryable(self):
        cfg = {"url": "http://h/v1", "model": "vl"}
        for code in (408, 429):
            self.patch(FakeResp(code, body="slow down"))
            with self.assertRaises(judge.RateLimited):
                judge.chat(cfg, "q")

    def test_retry_waits_for_a_rate_limit_and_succeeds(self):
        sleeps = []
        orig = judge.time.sleep
        judge.time.sleep = sleeps.append
        self.addCleanup(setattr, judge.time, "sleep", orig)
        self.patch(FakeResp(429, body="slow"), FakeResp(content='{"1": "yes"}'))
        cfg = {"url": "http://h/v1", "model": "vl"}
        self.assertEqual(judge.retry(lambda: judge.parse_answers(judge.chat(cfg, "q"), 1)), ["yes"])
        self.assertEqual(sleeps, [5])

    def test_retry_after_header_is_capped(self):
        self.assertEqual(judge.retry_after(FakeResp(429, body="", headers={"Retry-After": "2"})), 2)
        self.assertEqual(judge.retry_after(FakeResp(429, body="", headers={"Retry-After": "600"})), 30)
        self.assertEqual(judge.retry_after(FakeResp(429, body="", headers={"Retry-After": "Wed, 21 Oct"})), 5)

    def test_retry_gives_up_when_stop_is_set(self):
        ev = threading.Event()
        ev.set()
        fake = self.patch(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            judge.retry(lambda: judge.chat({"url": "http://h/v1", "model": "vl"}, "q"), ev)
        self.assertEqual(len(fake.calls), 1)

    def test_parse_ignores_thoughts(self):
        self.assertEqual(judge.parse_questions('<think>Format: ["Is it a cat?"]</think> ["Is it a dog?"]'),
                         ["Is it a dog?"])
        self.assertEqual(judge.parse_answers('<Think>e.g. {"1": "yes"}</Think>{"1": "no"}', 1), ["no"])

    def test_parse_questions_skips_a_numbered_reference(self):
        self.assertEqual(judge.parse_questions('As in [1], use: ["A?"]'), ["A?"])

    def test_three_failures_in_a_row_stop_the_job(self):
        for gid in ("g1", "g2", "g3", "g4"):
            self.add_gen(gid)
        self.patch(*[FakeResp(500, body="down")] * 8)
        s = self.run_judge()
        self.assertEqual((s["done"], s["failed"], s["stopped"]), (0, 3, True))
        self.assertEqual([d for ev, d in self.events if ev == "abort"][0]["msg"].startswith("3 failures in a row"), True)
        self.assertEqual(len(self.fake.calls), 6)   # two attempts per image, then stop

    def test_image_content_uses_the_file_type(self):
        f = self.tmp / "x.webp"
        f.write_bytes(b"webp")
        url = judge.image_content(f, ["Q?"])[1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/webp;base64,"))

    def test_parse_answers_braces_before_json(self):
        self.assertEqual(judge.parse_answers('Thinking {it is red}. Answer: {"1": "yes"}', 1), ["yes"])

    def test_parse_questions_trailing_brackets(self):
        self.assertEqual(judge.parse_questions('["A?", "B?"] (see [1])'), ["A?", "B?"])
        self.assertEqual(judge.parse_questions('Ideas [draft] → ["A?"]'), ["A?"])


class T_ui_data(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cb-judge-ui-"))
        (self.tmp / "m").mkdir()
        self.p = mk_prompt()
        self.store = bench.Store(self.tmp)
        for gid in ("a", "b"):
            (self.tmp / "m" / f"{gid}.png").write_bytes(b"png")
            self.store.append(mk_gen(gid, self.p))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def data(self, cfg):
        orig = bench.out_dir
        bench.out_dir = lambda c: self.tmp
        try:
            return bench.ui_data({"models": {}, **cfg}, [self.p], self.store, False)
        finally:
            bench.out_dir = orig

    def test_ui_data_judge_fields(self):
        _, info = judge.expected(mk_gen("a", self.p), {"p": self.p}, JCFG, self.tmp)
        self.store.set_judgment("a", {"qhash": info["qhash"], "fidelity_score": 1.0})
        self.store.set_judgment("b", {"qhash": "wrong", "fidelity_score": 0.0})
        d = self.data({"judge": JCFG})
        self.assertTrue(d["judge_enabled"])
        self.assertFalse(d["judge"]["a"]["stale"])
        self.assertTrue(d["judge"]["b"]["stale"])
        self.assertEqual(d["judge"]["a"]["fidelity_score"], 1.0)
        self.assertEqual(d["prompts"][0]["judge"], ["Is the cat {emotion}?"])

    def test_ui_data_without_judge(self):
        self.store.set_judgment("a", {"qhash": "x"})
        d = self.data({})
        self.assertFalse(d["judge_enabled"])
        self.assertEqual(d["judge"], {})


class T_runner(unittest.TestCase):
    def test_runner_judge_kind(self):
        tmp = Path(tempfile.mkdtemp(prefix="cb-judge-runner-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "config.yaml").write_text("models: {}\njudge: {url: u, model: m}\n", encoding="utf-8")
        (tmp / "prompts.yaml").write_text("prompts: []\n", encoding="utf-8")
        seen = {}

        def fake_run(cfg, prompts, path, opts, emit, stop=None):
            seen.update(opts=opts, path=path)
            emit("plan", total=1)
            emit("start", n=1, label="m · p · s1")
            emit("ok", n=1, label="m · p · s1", secs=0.5, rec={"id": "g", "files": ["m/g.png"], "model": "m",
                                                               "prompt": "p", "variant": {}, "seed": 1},
                 note="fidelity 1/1")
            s = {"total": 1, "done": 1, "skipped": 0, "failed": 0, "idle": 0, "stopped": False}
            emit("end", **s)
            return s
        orig = judge.run
        judge.run = fake_run
        self.addCleanup(setattr, judge, "run", orig)
        r = bench.Runner(tmp / "config.yaml", tmp / "prompts.yaml")
        r.start({"questions_only": True}, "judge")
        r.thread.join(5)
        st = r.status()
        self.assertEqual((st["kind"], st["state"], st["done"]), ("judge", "done", 1))
        self.assertTrue(any("fidelity 1/1" in l["msg"] for l in st["log"]))
        self.assertEqual(seen["opts"], {"questions_only": True})
        self.assertEqual(seen["path"], tmp / "prompts.yaml")

    def test_runner_run_then_judge(self):
        tmp = Path(tempfile.mkdtemp(prefix="cb-judge-runner-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "config.yaml").write_text("models: {}\njudge: {url: u, model: m}\n", encoding="utf-8")
        (tmp / "prompts.yaml").write_text("prompts: []\n", encoding="utf-8")
        seen = {}
        orig_exec, orig_run = bench.execute, judge.run

        def fake_exec(cfg, prompts, opts, emit, stop=None, abort=None):
            emit("queued", prompt_id="comfy-123")
            return {"total": 1, "done": 1, "skipped": 0, "failed": 0, "idle": 0, "stopped": False, "new_ids": ["g1"]}
        bench.execute = fake_exec
        judge.run = lambda cfg, prompts, path, opts, emit, stop=None: seen.update(opts, prompt_id=r.prompt_id) or {
            "total": 1, "done": 1, "skipped": 0, "failed": 0, "idle": 0, "stopped": False}
        self.addCleanup(setattr, bench, "execute", orig_exec)
        self.addCleanup(setattr, judge, "run", orig_run)
        r = bench.Runner(tmp / "config.yaml", tmp / "prompts.yaml")
        r.start({"judge": True, "dry_run": False})
        r.thread.join(5)
        self.assertEqual(seen["ids"], {"g1"})
        self.assertIsNone(seen["prompt_id"])      # a hard stop while judging must not cancel ComfyUI work
        self.assertEqual(r.status()["state"], "done")


class T_api_judge(unittest.TestCase):
    def test_api_judge_start(self):
        import argparse
        import threading
        import urllib.request

        class StubRunner:
            calls = []

            def start(self, opts, kind="run"):
                self.calls.append((opts, kind))

            def status(self):
                return {"state": "running", "kind": "judge"}
        runner = StubRunner()
        args = argparse.Namespace(config=bench.ROOT / "config.yaml", prompts_file=bench.ROOT / "prompts.yaml")
        srv = bench.ThreadingHTTPServer(("127.0.0.1", 0), bench.make_handler(args, runner))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        body = json.dumps({"models": ["m"], "prompts": ["p"], "category": "Text, Art", "force": True,
                           "questions_only": True}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/judge/start", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(json.loads(r.read())["kind"], "judge")
        opts, kind = runner.calls[0]
        self.assertEqual(kind, "judge")
        self.assertEqual(opts, {"models": ["m"], "prompts": ["p"], "categories": ["Text", "Art"],
                                "force": True, "questions_only": True})

    def test_run_opts_judge(self):
        self.assertTrue(bench.run_opts({"judge": True})["judge"])
        self.assertFalse(bench.run_opts({})["judge"])


class KeyBase(unittest.TestCase):
    """Temp data dir as bench.ROOT, no key in the environment."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cb-key-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        orig_root = bench.ROOT
        bench.ROOT = self.tmp
        self.addCleanup(setattr, bench, "ROOT", orig_root)
        old = os.environ.pop(judge.KEY_ENV, None)
        self.addCleanup(lambda: os.environ.pop(judge.KEY_ENV, None) if old is None
                        else os.environ.__setitem__(judge.KEY_ENV, old))


class T_key(KeyBase):
    def test_key_file_roundtrip(self):
        self.assertEqual(judge.key_source(), "none")
        self.assertEqual(judge.api_key(), "")
        judge.set_key("  sk-file \n")
        f = self.tmp / judge.KEY_FILE
        self.assertEqual(f.read_text(encoding="utf-8"), "sk-file")
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual(judge.key_source(), "file")
        self.assertEqual(judge.api_key(), "sk-file")
        judge.set_key("")
        self.assertFalse(f.exists())
        self.assertEqual(judge.key_source(), "none")
        judge.set_key("")   # deleting a missing key is fine

    def test_env_beats_file(self):
        judge.set_key("sk-file")
        os.environ[judge.KEY_ENV] = "sk-env"
        self.assertEqual(judge.key_source(), "env")
        self.assertEqual(judge.api_key(), "sk-env")

    def test_chat_uses_key_file(self):
        judge.set_key("sk-file")
        fake = FakePost(FakeResp(content="OK"))
        orig = judge.requests.post
        judge.requests.post = fake
        self.addCleanup(setattr, judge.requests, "post", orig)
        judge.chat({"url": "http://h/v1", "model": "vl"}, "q")
        self.assertEqual(fake.calls[0]["headers"]["Authorization"], "Bearer sk-file")

    def test_test_connection(self):
        orig = judge.requests.post
        self.addCleanup(setattr, judge.requests, "post", orig)
        judge.requests.post = FakePost(FakeResp(content="OK"))
        r = judge.test_connection({"url": "http://h/v1", "model": "vl"})
        self.assertTrue(r["ok"])
        self.assertIn("seconds", r)
        self.assertEqual(r["reply"], "OK")
        judge.requests.post = FakePost(FakeResp(401, body="bad key"))
        r = judge.test_connection({"url": "http://h/v1", "model": "vl"})
        self.assertFalse(r["ok"])
        self.assertIn("401", r["error"])
        judge.requests.post = FakePost(judge.requests.Timeout("slow"))
        self.assertFalse(judge.test_connection({"url": "http://h/v1", "model": "vl"})["ok"])
        with self.assertRaises(BenchError):
            judge.test_connection({"url": " ", "model": "vl"})


class T_api_key(KeyBase):
    def setUp(self):
        super().setUp()
        import argparse
        (self.tmp / "config.yaml").write_text("models: {}\n", encoding="utf-8")
        (self.tmp / "prompts.yaml").write_text("prompts: []\n", encoding="utf-8")
        args = argparse.Namespace(config=self.tmp / "config.yaml", prompts_file=self.tmp / "prompts.yaml")
        srv = bench.ThreadingHTTPServer(("127.0.0.1", 0), bench.make_handler(args, None))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(self, method, path, body=None):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_key_status_never_leaks(self):
        self.assertEqual(self.call("GET", "/api/setup")[1]["judge_key"], "none")
        code, res = self.call("PUT", "/api/setup/judge-key", {"key": "sk-secret"})
        self.assertEqual(code, 200)
        self.assertEqual(res["judge_key"], "file")
        _, data = self.call("GET", "/api/setup")
        self.assertEqual(data["judge_key"], "file")
        self.assertNotIn("sk-secret", json.dumps(data))
        self.assertEqual(self.call("PUT", "/api/setup/judge-key", {"key": ""})[1]["judge_key"], "none")
        self.assertFalse((self.tmp / judge.KEY_FILE).exists())

    def test_key_put_refused_when_env_set(self):
        os.environ[judge.KEY_ENV] = "sk-env"
        code, _ = self.call("PUT", "/api/setup/judge-key", {"key": "x"})
        self.assertEqual(code, 400)
        self.assertFalse((self.tmp / judge.KEY_FILE).exists())

    def test_judge_test_endpoint(self):
        orig = judge.requests.post
        self.addCleanup(setattr, judge.requests, "post", orig)
        judge.requests.post = FakePost(FakeResp(content="OK"))
        code, res = self.call("POST", "/api/judge/test", {"url": "http://h/v1", "model": "vl"})
        self.assertEqual((code, res["ok"]), (200, True))
        judge.requests.post = FakePost(judge.requests.ConnectionError("refused"))
        code, res = self.call("POST", "/api/judge/test", {"url": "http://h/v1", "model": "vl"})
        self.assertEqual((code, res["ok"]), (200, False))
        self.assertIn("not reachable", res["error"])
        code, _ = self.call("POST", "/api/judge/test", {"url": "", "model": "vl"})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
