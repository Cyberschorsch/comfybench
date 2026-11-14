"""ComfyBench judge – a vision LLM answers fixed yes/no questions per image.

Uses bench.* at call time (bench registers itself as module "bench" before importing this one).
"""
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
