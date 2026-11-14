"""ComfyBench judge – a vision LLM answers fixed yes/no questions per image.

Uses bench.* at call time (bench registers itself as module "bench" before importing this one).
"""
import base64
import hashlib
import json
import os
import re
import time
from collections import Counter

import requests

import bench

KEY_ENV = "COMFYBENCH_JUDGE_KEY"
TIMEOUT = 120


def judge_cfg(cfg):
    return cfg.get("judge") or None


def check_questions(qs, prompt):
    """Raises BenchError unless qs is a non-empty list of questions using only the prompt's variables."""
    pid = prompt.get("id", "?")
    if not isinstance(qs, list) or not qs:
        raise bench.BenchError(f"{pid}: judge must be a non-empty list of questions")
    known = set(prompt.get("vars") or {})
    for q in qs:
        if not isinstance(q, str) or not q.strip():
            raise bench.BenchError(f"{pid}: judge questions must be non-empty text")
        unknown = set(bench.PROMPT_VAR.findall(q)) - known
        if unknown:
            raise bench.BenchError(f"{pid}: judge question uses unknown variable {{{sorted(unknown)[0]}}}")


def render_questions(qs, variant):
    return [bench.render_text(q, variant) for q in qs]


def qhash(model, fidelity, quality):
    src = json.dumps({"judge": model, "fidelity": fidelity, "quality": quality}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(src.encode()).hexdigest()[:12]


def _first_json(text, pattern):
    m = re.search(pattern, text or "", re.S)
    if not m:
        raise ValueError("no JSON found in the answer")
    return json.loads(m.group(0))   # json.JSONDecodeError is a ValueError


def parse_answers(text, n):
    """'{"1": "yes", "2": "no"}' (possibly wrapped in prose or a fence) → ["yes", "no"]."""
    data = _first_json(text, r"\{.*?\}")
    out = []
    for i in range(1, n + 1):
        a = data.get(str(i)) if isinstance(data, dict) else None
        a = a.strip().lower() if isinstance(a, str) else None
        if a not in ("yes", "no"):
            raise ValueError(f"answer {i} missing or not yes/no")
        out.append(a)
    return out


def parse_questions(text):
    data = _first_json(text, r"\[.*\]")
    if not isinstance(data, list) or not all(isinstance(q, str) for q in data):
        raise ValueError("expected a JSON list of questions")
    return data


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


def chat(jcfg, content):
    """One chat completion; returns the reply text."""
    url = jcfg["url"].rstrip("/") + "/chat/completions"
    key = os.environ.get(KEY_ENV, "").strip()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": jcfg["model"], "temperature": 0, "messages": [{"role": "user", "content": content}]}
    try:
        r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
    except requests.ConnectionError:
        raise JudgeAbort(f"Judge at {jcfg['url']} is not reachable.") from None
    except requests.Timeout:
        raise RuntimeError(f"Judge did not answer within {TIMEOUT}s") from None
    if 400 <= r.status_code < 500:
        raise JudgeAbort(f"Judge returned HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code >= 500:
        raise RuntimeError(f"Judge returned HTTP {r.status_code}: {r.text[:300]}")
    try:
        return r.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        raise RuntimeError(f"Judge sent an unexpected response: {r.text[:300]}") from None


def retry(fn):
    """fn() with one more attempt on a transient error or an unusable answer."""
    try:
        return fn()
    except (RuntimeError, ValueError):
        return fn()


IMAGE_INSTRUCTION = ('Answer each question about the image with yes or no. Reply with only a JSON object '
                     'mapping the question number to "yes" or "no", e.g. {"1": "yes", "2": "no"}.')


def image_content(path, questions):
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    text = IMAGE_INSTRUCTION + "\n\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    return [{"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}]


def questions_content(prompt):
    names = sorted(prompt.get("vars") or {})
    parts = [
        "Write 3 to 8 yes/no questions in English that check whether an image follows this text-to-image prompt. "
        "Each question checks one visible fact (subject, count, color, position, style, text in the image). "
        "A 'yes' answer must mean the prompt was followed.",
        f"Prompt: {prompt['prompt']}",
    ]
    if names:
        parts.append("The prompt has variables: " + ", ".join("{" + n + "}" for n in names)
                     + ". Where a question depends on one, keep the placeholder exactly as written, e.g. {"
                     + names[0] + "}.")
    if prompt.get("check"):
        parts.append(f"The person benchmarking wants to check: {prompt['check']}")
    parts.append('Reply with only a JSON list of strings, e.g. ["Is there a cat?", "Is the cat orange?"].')
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
            except bench.BenchError as e:
                raise ValueError(str(e)) from None
            return qs
        try:
            new[p["id"]] = retry(ask)
        except JudgeAbort as e:
            emit("abort", msg=str(e))
            stopped = True
            break
        except (RuntimeError, ValueError) as e:
            failed += 1
            emit("fail", n=n, label=p["id"], msg=str(e))
            continue
        emit("ok", n=n, label=p["id"], secs=round(time.time() - t0, 1), rec=None,
             note=f"{len(new[p['id']])} questions")
    if new:   # one write, with backup – write_yaml drops comments in prompts.yaml
        doc = bench.load_yaml(prompts_path)
        for entry in doc.get("prompts") or []:
            if entry.get("id") in new and not entry.get("judge"):
                entry["judge"] = new[entry["id"]]
        doc["prompts"] = [bench._ordered(e, bench.PROMPT_KEY_ORDER) for e in doc.get("prompts") or []]
        bench.validate_prompts(doc)
        bench.write_yaml(prompts_path, doc, bench.PROMPTS_HEADER)
    summary = _summary(len(todo), len(new), failed, stopped, questions=len(new))
    emit("end", **summary)
    return summary


def _label(g):
    return " · ".join(x for x in (g.get("model"), g.get("prompt"), g.get("variant_key"), f"s{g.get('seed')}") if x)


def _judge(cfg, jcfg, prompts, opts, emit, stop):
    base = bench.out_dir(cfg)
    store = bench.Store(base)
    todo, skipped_by = select(store.generations(), prompts, jcfg, base, store.judgments(), opts)
    emit("plan", total=len(todo))
    done = failed = 0
    stopped = False
    for n, (g, info) in enumerate(todo, 1):
        if stop is not None and stop.is_set():
            stopped = True
            break
        label = _label(g)
        emit("start", n=n, label=label)
        t0 = time.time()
        qs = info["fidelity"] + info["quality"]
        try:
            answers = retry(lambda: parse_answers(chat(jcfg, image_content(base / g["files"][0], qs)), len(qs)))
        except JudgeAbort as e:
            emit("abort", msg=str(e))
            stopped = True
            break
        except (RuntimeError, ValueError) as e:
            failed += 1
            emit("fail", n=n, label=label, msg=str(e))
            continue
        k = len(info["fidelity"])
        fid, qual = [list(x) for x in zip(qs[:k], answers[:k])], [list(x) for x in zip(qs[k:], answers[k:])]
        store.set_judgment(g["id"], {
            "at": bench.now(), "judge": jcfg["model"], "qhash": info["qhash"],
            "fidelity": fid, "quality": qual, "fidelity_score": score(fid), "quality_score": score(qual)})
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
