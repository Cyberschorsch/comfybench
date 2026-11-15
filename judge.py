"""ComfyBench judge – a vision LLM answers fixed yes/no questions per image.

Uses bench.* at call time (bench registers itself as module "bench" before importing this one).
"""
import base64
import hashlib
import json
import mimetypes
import os
import re
import time
from collections import Counter

import requests

import bench

KEY_ENV = "COMFYBENCH_JUDGE_KEY"
TIMEOUT = 120
MAX_FAILS = 3   # failures in a row after which the job stops – usually the judge endpoint, not the images


def judge_cfg(cfg):
    return cfg.get("judge") or None


# ---------------------------------------------------------------- API key
# The key never goes into config.yaml: the environment wins, else a file in the data folder (set in Setup → Judge).
KEY_FILE = ".judge-key"


def key_path():
    return bench.ROOT / KEY_FILE


def key_source():
    """'env', 'file' or 'none' – the key itself is never sent to the UI."""
    if os.environ.get(KEY_ENV, "").strip():
        return "env"
    return "file" if api_key() else "none"


def api_key():
    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        return key
    try:
        return key_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_key(key):
    """Write the key file (owner-only); an empty key deletes it."""
    key, path = (key or "").strip(), key_path()
    if not key:
        path.unlink(missing_ok=True)
        return
    path.unlink(missing_ok=True)   # O_CREAT keeps an existing file's mode – start fresh
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)


def test_connection(jcfg):
    """One tiny text request with url/model from the (possibly unsaved) form.
    {ok, seconds, reply} or {ok: False, error}; checks reachability, key and model name – not vision."""
    url, model = (str(jcfg.get(k) or "").strip() for k in ("url", "model"))
    if not url or not model:
        raise bench.BenchError("Enter url and model first.")
    t0 = time.monotonic()
    try:
        reply = chat({"url": url, "model": model}, "Reply with the single word OK.")
    except (RuntimeError, bench.BenchError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "seconds": round(time.monotonic() - t0, 2), "reply": reply.strip()[:200]}


def check_questions(qs, prompt):
    """Raises BenchError unless qs is a non-empty list of questions using only the prompt's variables."""
    pid = prompt.get("id", "?")
    if not isinstance(qs, list) or not qs:
        raise bench.BenchError(f"{pid}: judge must be a non-empty list of questions")
    known = set(prompt.get("vars") or {})
    for q in qs:
        if not isinstance(q, str) or not q.strip():
            raise bench.BenchError(f"{pid}: judge questions must be non-empty text")
        for name, part in bench.PROMPT_VAR.findall(q):
            if name not in known:
                raise bench.BenchError(f"{pid}: judge question uses unknown variable {{{name}"
                                       + (f":{part}" if part else "") + "}")
            if part and part != "label":
                raise bench.BenchError(f"{pid}: judge question uses {{{name}:{part}}} – only :label is allowed")


def render_questions(qs, variant):
    return [bench.render_text(q, variant) for q in qs]


def qhash(model, fidelity, quality):
    src = json.dumps({"judge": model, "fidelity": fidelity, "quality": quality}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(src.encode()).hexdigest()[:12]


def _first_json(text, kind, ok=None):
    """First JSON value of kind (dict or list) in text – models wrap it in prose, fences or thoughts.
    ok(val) rejects a candidate, so the next one is tried."""
    dec = json.JSONDecoder()
    text = re.sub(r"(?is)<think(?:ing)?>.*?</think(?:ing)?>", "", text or "")
    for m in re.finditer(r"[\[{]", text):
        try:
            val, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(val, kind) and (ok is None or ok(val)):
            return val
    raise ValueError("no JSON found in the answer")


def parse_answers(text, n):
    """'{"1": "yes", "2": "no"}' (possibly wrapped in prose or a fence) → ["yes", "no"]."""
    data = _first_json(text, dict)
    out = []
    for i in range(1, n + 1):
        a = data.get(str(i))
        a = a.strip().lower() if isinstance(a, str) else None
        if a not in ("yes", "no"):
            raise ValueError(f"answer {i} missing or not yes/no")
        out.append(a)
    return out


def parse_questions(text):
    return _first_json(text, list, lambda v: all(isinstance(q, str) for q in v))


def score(pairs):
    return sum(a == "yes" for _, a in pairs) / len(pairs) if pairs else None


def match_variant(prompt, gen):
    gv = gen.get("variant") or {}
    for v in prompt["_variants"]:
        if all(str(gv.get(name)) == str(x["label"]) for name, x in v.items()):
            return v
    return None


def expected(gen, prompts_by_id, jcfg, base):
    """(reason, None) if the generation cannot be judged now, else (None, {fidelity, quality, qhash})."""
    p = prompts_by_id.get(gen.get("prompt"))
    if p is None:
        return "no-prompt", None
    if not p.get("judge"):
        return "no-questions", None
    if gen.get("prompt_rev") and gen["prompt_rev"] != bench.prompt_rev(p):
        return "old-wording", None
    v = match_variant(p, gen)
    if v is None:
        return "unknown-variant", None
    if not gen.get("files") or not (base / gen["files"][0]).is_file():
        return "missing-file", None
    fid = render_questions(p["judge"], v)
    qual = list(jcfg.get("quality") or [])
    return None, {"fidelity": fid, "quality": qual, "qhash": qhash(jcfg["model"], fid, qual)}


def select(gens, prompts, jcfg, base, judgments, opts):
    """Generations to judge as [(gen, expected-info)], plus a Counter of skip reasons."""
    by_id = {p["id"]: p for p in prompts}
    models, pids, cats, ids = opts.get("models"), opts.get("prompts"), opts.get("categories"), opts.get("ids")
    todo, skipped = [], Counter()
    for g in gens:
        if g.get("status") != "ok" or g.get("mode") == "import":
            continue
        if (models and g.get("model") not in models) or (pids and g.get("prompt") not in pids) \
                or (ids is not None and g.get("id") not in ids):
            continue
        if cats and (by_id.get(g.get("prompt")) or {}).get("category") not in cats:
            continue
        reason, info = expected(g, by_id, jcfg, base)
        if reason is None and not opts.get("force") and (judgments.get(g["id"]) or {}).get("qhash") == info["qhash"]:
            reason = "current"
        if reason:
            skipped[reason] += 1
        else:
            todo.append((g, info))
    return todo, skipped


class JudgeAbort(bench.BenchError):
    """The judge endpoint is unusable (unreachable, 4xx) – the whole job stops."""


class RateLimited(RuntimeError):
    """A 408/429 answer: worth one more try after a short wait."""

    def __init__(self, msg, wait):
        super().__init__(msg)
        self.wait = wait


def retry_after(r):
    """Seconds to wait before retrying a rate-limited request; an HTTP-date Retry-After is ignored."""
    try:
        s = float((getattr(r, "headers", None) or {}).get("Retry-After") or 0)
    except (TypeError, ValueError):
        s = 0
    return min(30, s or 5)


def chat(jcfg, content):
    """One chat completion; returns the reply text."""
    url = jcfg["url"].rstrip("/") + "/chat/completions"
    key = api_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": jcfg["model"], "temperature": 0, "messages": [{"role": "user", "content": content}]}
    try:
        r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
    except requests.ConnectionError:
        raise JudgeAbort(f"Judge at {jcfg['url']} is not reachable.") from None
    except requests.Timeout:
        raise RuntimeError(f"Judge did not answer within {TIMEOUT}s") from None
    except (requests.exceptions.InvalidURL, requests.exceptions.MissingSchema, requests.exceptions.InvalidSchema) as e:
        raise JudgeAbort(f"Judge url {jcfg['url']!r} is invalid: {e}") from None
    except requests.RequestException as e:
        raise RuntimeError(f"Judge request failed: {e}") from None
    if r.status_code in (408, 429):   # request timeout / rate limit: wait, then one more try
        raise RateLimited(f"Judge returned HTTP {r.status_code}: {r.text[:300]}", retry_after(r))
    if 400 <= r.status_code < 500:
        raise JudgeAbort(f"Judge returned HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code >= 500:
        raise RuntimeError(f"Judge returned HTTP {r.status_code}: {r.text[:300]}")
    try:
        return r.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        raise RuntimeError(f"Judge sent an unexpected response: {r.text[:300]}") from None


def retry(fn, stop=None):
    """fn() with one more attempt on a transient error or an unusable answer.
    A rate limit is waited out first; a stop request skips the second attempt."""
    try:
        return fn()
    except (RuntimeError, ValueError) as e:
        if stop is not None and stop.is_set():
            raise
        if isinstance(e, RateLimited):
            time.sleep(e.wait)
        return fn()


IMAGE_INSTRUCTION = ('Answer each question about the image with yes or no. Reply with only a JSON object '
                     'mapping the question number to "yes" or "no", e.g. {"1": "yes", "2": "no"}.')


def image_content(path, questions):
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    text = IMAGE_INSTRUCTION + "\n\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    return [{"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}]


MAX_VAR_EXAMPLES = 6


def _var_examples(prompt):
    """{name: [(label, value), …]} from the prompt's variants, at most MAX_VAR_EXAMPLES per name."""
    out = {}
    for v in prompt.get("_variants") or []:
        for n, x in v.items():
            pairs = out.setdefault(n, [])
            if len(pairs) < MAX_VAR_EXAMPLES and (x["label"], x["value"]) not in pairs:
                pairs.append((x["label"], x["value"]))
    return out


def _var_summary(names, examples):
    """'{emotion} = laughing (laughing heartily), angry' – shows the model what gets filled in."""
    out = []
    for n in names:
        pairs = examples.get(n) or []
        listed = ", ".join(lab if str(val) == lab else f"{lab} ({str(val)[:60]})" for lab, val in pairs)
        out.append("{" + n + "}" + (" = " + listed if listed else ""))
    return "; ".join(out)


def text_vars(prompt):
    """Variable names that appear in the prompt or negative text – the ones a question can check."""
    text = f"{prompt.get('prompt') or ''} {prompt.get('negative') or ''}"
    names = {m.group(1) for m in bench.PROMPT_VAR.finditer(text)}
    return names & set(prompt.get("vars") or {})


def question_vars(qs):
    return {name for q in qs for name, _ in bench.PROMPT_VAR.findall(q)}


def questions_content(prompt):
    names = sorted(prompt.get("vars") or {})
    varying = sorted(text_vars(prompt))     # variables the prompt text actually varies by
    parts = [
        "Write 3 to 8 yes/no questions in English that check whether an image follows this text-to-image prompt. "
        "Each question checks one visible fact (subject, count, color, position, style, text in the image). "
        "A 'yes' answer must mean the prompt was followed.",
        f"Prompt: {prompt['prompt']}",
    ]
    if names:
        parts.append("The prompt has variables: " + _var_summary(names, _var_examples(prompt))
                     + ". They are filled in per image; a question that depends on one must contain it.")
        parts.append("Write '{" + names[0] + "}' where the whole text fits the question, and '{"
                     + names[0] + ":label}' for the short label – use the label when the value is a long "
                     "phrase and the question reads better with the short name. A question whose variable is "
                     "not varied in the prompt text (e.g. a workflow value like denoise) needs no variable.")
    if varying:                     # without this spelled out per name the model inlines the value
        parts.append("Use the placeholders literally: " + ", ".join("{" + n + "}" for n in varying)
                     + ". " + " ".join(f"At least one question must contain {{{n}}}." for n in varying)
                     + " Never replace a placeholder by its value – not 'Is she angry?' but "
                     "'Is she {emotion:label}' (or '{emotion}' where the value reads well in the sentence).")
    if prompt.get("check"):
        parts.append(f"The person benchmarking wants to check: {prompt['check']}")
    example = ('["Is there a cat?", ' + ", ".join(f'"Is the cat {{{n}}}?"' for n in varying) + "]"
               if varying else '["Is there a cat?", "Is the cat orange?"]')
    parts.append("Reply with only a JSON list of strings, e.g. " + example + ".")
    return "\n\n".join(parts)


EXAMPLE = """Add a judge section to config.yaml, e.g.:

judge:
  url: http://127.0.0.1:11434/v1   # any OpenAI-compatible endpoint
  model: qwen2.5vl:7b
  quality:
    - Is the image free of visible artifacts?"""


def run(cfg, prompts, prompts_path, opts, emit, stop=None):
    """Judge job for CLI and server. opts: models, prompts, categories, force, questions_only, ids.
    Emits the same events as bench.execute."""
    jcfg = judge_cfg(cfg)
    if not jcfg:
        raise bench.BenchError("No judge configured. " + EXAMPLE)
    if opts.get("questions_only"):
        return _questions(jcfg, prompts, prompts_path, opts, emit, stop)
    return _judge(cfg, jcfg, prompts, opts, emit, stop)


def _summary(total, done, failed, stopped, skipped_by=None, **extra):
    skipped_by = dict(skipped_by or {})
    return {"total": total, "done": done, "skipped": sum(skipped_by.values()), "failed": failed, "idle": 0,
            "stopped": stopped, "skipped_by": skipped_by, **extra}


def _questions(jcfg, prompts, prompts_path, opts, emit, stop):
    pids, cats = opts.get("prompts"), opts.get("categories")
    todo = [p for p in prompts if not p.get("judge")
            and (not pids or p["id"] in pids) and (not cats or p.get("category") in cats)]
    emit("plan", total=len(todo))
    new, failed, stopped = {}, 0, False
    try:
        failed, stopped = _ask_all(jcfg, todo, new, emit, stop)
    finally:   # questions generated before an unexpected error are kept
        if new:
            _write_questions(prompts_path, new)
    summary = _summary(len(todo), len(new), failed, stopped, questions=len(new))
    emit("end", **summary)
    return summary


def _ask_all(jcfg, todo, new, emit, stop):
    failed, stopped, streak = 0, False, 0
    for n, p in enumerate(todo, 1):
        if stop is not None and stop.is_set():
            stopped = True
            break
        emit("start", n=n, label=p["id"])
        t0 = time.time()

        def ask():
            qs = parse_questions(chat(jcfg, questions_content(p)))
            try:
                check_questions(qs, p)
                missing = text_vars(p) - question_vars(qs)
                if missing:
                    raise bench.BenchError("no question uses " + ", ".join("{" + n + "}" for n in sorted(missing))
                                           + ", though the prompt text varies by it")
            except bench.BenchError as e:
                raise ValueError(str(e)) from None
            return qs
        try:
            new[p["id"]] = retry(ask, stop)
        except JudgeAbort as e:
            emit("abort", msg=str(e))
            stopped = True
            break
        except (RuntimeError, ValueError) as e:
            failed += 1
            streak += 1
            emit("fail", n=n, label=p["id"], msg=str(e))
            if streak >= MAX_FAILS:   # a broken endpoint, not a few bad prompts
                emit("abort", msg=f"{streak} failures in a row, last: {e}")
                stopped = True
                break
            continue
        streak = 0
        emit("ok", n=n, label=p["id"], secs=round(time.time() - t0, 1), rec=None,
             note=f"{len(new[p['id']])} questions")
    return failed, stopped


def _write_questions(prompts_path, new):
    """One write, with backup – write_yaml drops comments in prompts.yaml. Lists added meanwhile by hand win."""
    doc = bench.load_yaml(prompts_path)
    for entry in doc.get("prompts") or []:
        if entry.get("id") in new and not entry.get("judge"):
            entry["judge"] = new[entry["id"]]
    doc["prompts"] = [bench._ordered(e, bench.PROMPT_KEY_ORDER) for e in doc.get("prompts") or []]
    bench.validate_prompts(doc)
    bench.write_yaml(prompts_path, doc, bench.PROMPTS_HEADER)


def _label(g):
    return " · ".join(x for x in (g.get("model"), g.get("prompt"), g.get("variant_key"), f"s{g.get('seed')}") if x)


def _judge(cfg, jcfg, prompts, opts, emit, stop):
    base = bench.out_dir(cfg)
    store = bench.Store(base)
    todo, skipped_by = select(store.generations(), prompts, jcfg, base, store.judgments(), opts)
    emit("plan", total=len(todo))
    done = failed = 0
    stopped = False
    streak = 0
    for n, (g, info) in enumerate(todo, 1):
        if stop is not None and stop.is_set():
            stopped = True
            break
        label = _label(g)
        emit("start", n=n, label=label)
        t0 = time.time()
        qs = info["fidelity"] + info["quality"]
        try:
            content = image_content(base / g["files"][0], qs)
            answers = retry(lambda: parse_answers(chat(jcfg, content), len(qs)), stop)
        except JudgeAbort as e:
            emit("abort", msg=str(e))
            stopped = True
            break
        except (RuntimeError, ValueError, OSError) as e:   # OSError: image deleted meanwhile
            failed += 1
            streak += 1
            emit("fail", n=n, label=label, msg=str(e))
            if streak >= MAX_FAILS:   # a broken endpoint, not a few bad images
                emit("abort", msg=f"{streak} failures in a row, last: {e}")
                stopped = True
                break
            continue
        streak = 0
        k = len(info["fidelity"])
        fid, qual = [list(x) for x in zip(qs[:k], answers[:k])], [list(x) for x in zip(qs[k:], answers[k:])]
        entry = {"at": bench.now(), "judge": jcfg["model"], "qhash": info["qhash"],
                 "fidelity": fid, "quality": qual, "fidelity_score": score(fid), "quality_score": score(qual)}
        if not store.set_judgment(g["id"], entry):
            continue   # deleted while it was being judged
        done += 1
        note = f"fidelity {sum(a == 'yes' for _, a in fid)}/{len(fid)}"
        if qual:
            note += f" · quality {sum(a == 'yes' for _, a in qual)}/{len(qual)}"
        emit("ok", n=n, label=label, secs=round(time.time() - t0, 1), rec=g, note=note)
    if skipped_by:
        emit("warn", msg="Skipped: " + ", ".join(f"{v} {k}" for k, v in sorted(skipped_by.items())))
    if skipped_by.get("no-questions"):
        emit("warn", msg=f"{skipped_by['no-questions']} image(s) belong to prompts without judge questions – "
                         "run bench.py judge --questions-only first.")
    if done:
        bench.write_static(cfg, prompts, store)
    summary = _summary(len(todo), done, failed, stopped, skipped_by)
    emit("end", **summary)
    return summary
