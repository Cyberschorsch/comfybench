"""ComfyBench judge – a vision LLM answers fixed yes/no questions per image.

Uses bench.* at call time (bench registers itself as module "bench" before importing this one).
"""
import hashlib
import json
import re
from collections import Counter

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
