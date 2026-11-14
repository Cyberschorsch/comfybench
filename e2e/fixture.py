"""Builds a ComfyBench data dir for the Playwright smoke tests: python3 e2e/fixture.py <dir>

Three models, one prompt, one image each: m1 judged (current, one "no"), m2 judged but stale, m3 not judged.
"""
import json
import os
import shutil
import struct
import sys
import zlib
from pathlib import Path

HOME = Path(sys.argv[1]).resolve()
shutil.rmtree(HOME, ignore_errors=True)
(HOME / "workflows").mkdir(parents=True)
os.environ["COMFYBENCH_HOME"] = str(HOME)
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import bench  # noqa: E402
import judge  # noqa: E402


def png(rgb, size=16):
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


shutil.copy(REPO / "workflows" / "example-sdxl.json", HOME / "workflows" / "example-sdxl.json")
(HOME / "config.yaml").write_text("""server: http://127.0.0.1:9
seeds: [1]
models:
  m1: {label: Model One, workflow: workflows/example-sdxl.json}
  m2: {label: Model Two, workflow: workflows/example-sdxl.json}
  m3: {label: Model Three, workflow: workflows/example-sdxl.json}
judge:
  url: http://127.0.0.1:9/v1
  model: fake-vl
  quality: ["Is it clean?"]
""", encoding="utf-8")
(HOME / "prompts.yaml").write_text("""prompts:
  - id: apple
    category: Food
    prompt: a red apple on a table
    judge: ["Is the apple red?"]
""", encoding="utf-8")

cfg, prompts = bench.load_all(HOME / "config.yaml", HOME / "prompts.yaml")
p = prompts[0]
base = bench.out_dir(cfg)
store = bench.Store(base)
for i, (gid, model, color) in enumerate([("g1", "m1", (200, 30, 30)), ("g2", "m2", (30, 200, 30)),
                                         ("g3", "m3", (30, 30, 200))]):
    rel = f"{model}/apple/base__s1__{gid}.png"
    (base / rel).parent.mkdir(parents=True, exist_ok=True)
    (base / rel).write_bytes(png(color))
    store.append({"id": gid, "status": "ok", "mode": "txt2img", "model": model, "prompt": "apple",
                  "variant": {}, "variant_key": "", "seed": 1, "lora_combo": "none", "lora_names": [],
                  "prompt_rev": bench.prompt_rev(p), "text": p["prompt"], "negative": "", "values": {},
                  "inputs": {}, "input_src": {}, "files": [rel], "seconds": 1.0 + i,
                  "at": f"2026-11-14T10:0{i}:00"})

_, info = judge.expected(store.generations()[0], {"apple": p}, cfg["judge"], base)
store.set_judgment("g1", {"at": "2026-11-14T11:00:00", "judge": "fake-vl", "qhash": info["qhash"],
                          "fidelity": [["Is the apple red?", "no"]], "quality": [["Is it clean?", "yes"]],
                          "fidelity_score": 0.0, "quality_score": 1.0})
store.set_judgment("g2", {"at": "2026-11-14T11:00:00", "judge": "fake-vl", "qhash": "outdated0000",
                          "fidelity": [["Is the apple red?", "yes"]], "quality": [["Is it clean?", "yes"]],
                          "fidelity_score": 1.0, "quality_score": 1.0})
store.set_rating("g1", 2, "")
store.set_rating("g2", 4, "")
bench.write_static(cfg, prompts, store)
print(json.dumps({"home": str(HOME)}))
