#!/usr/bin/env python3
"""ComfyBench – run fixed prompt sets against multiple ComfyUI models,
store everything permanently in a gallery and compare side by side.

  python bench.py run                          # process everything, existing results are skipped
  python bench.py run --models flux-dev        # only specific models
  python bench.py run --prompts portrait-emotion --var emotion=laughing,angry
  python bench.py run --sweep cfg=3,5,7        # extra axis for all jobs
  python bench.py run --dry-run                # only fill and check workflows
  python bench.py import ~/ComfyUI/output      # bring own images into the gallery
  python bench.py serve                        # gallery, comparison and setup in the browser
  python bench.py check                        # trial-fill all model workflows
  python bench.py report                       # static copy as results/index.html
"""
import argparse
import datetime as dt
import hashlib
import itertools
import base64
import shutil
import json
import mimetypes
import os
import re
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
import yaml

CODE = Path(__file__).resolve().parent
# Data folder (config.yaml, prompts.yaml, workflows/, inputs/, results/). Default: next to the script.
ROOT = Path(os.environ.get("COMFYBENCH_HOME") or CODE).resolve()
PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")   # in workflow: {{seed}}
PROMPT_VAR = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")  # in prompt:   {emotion}
IMAGE_KEY = re.compile(r"^(image|mask)\w*$")        # keys whose values are input images
DATA_MARK = "/*__DATA__*/null"
SLUG = re.compile(r"^[\w.-]+$")


class BenchError(Exception):
    """Error in config, prompts or workflow – shown as a message in CLI and UI."""
INTERNAL_KEYS = {"prompt", "negative", "model_id", "prompt_id", "variant"}


# ================================================================ Loading

def load_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def load_all(config_path, prompts_path):
    cfg = load_yaml(config_path)
    pdata = load_yaml(prompts_path)
    shared = pdata.get("vars") or {}
    prompts = pdata.get("prompts") or []
    ids = [p["id"] for p in prompts]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise BenchError(f"Duplicate prompt IDs: {', '.join(sorted(dupes))}")
    for p in prompts:
        p["_variants"] = expand_variants(p, shared)
        p["_mode"] = prompt_mode(p)
    return cfg, prompts


def out_dir(cfg):
    return (ROOT / cfg.get("output_dir", "results")).resolve()


def prompt_mode(p):
    if p.get("mode"):
        return p["mode"]
    keys = list(p) + list(p.get("vars") or {}) + list(p.get("params") or {})
    return "img2img" if any(IMAGE_KEY.match(k) for k in keys) else "txt2img"


def wf_spec(spec):
    """Normalize workflow spec: "path.json" or {file: path.json, map: {placeholder: [node/input, …]}}."""
    if not spec:
        return None
    if isinstance(spec, str):
        return {"file": spec, "map": {}}
    if isinstance(spec, dict) and spec.get("file"):
        return {"file": spec["file"], "map": spec.get("map") or {}}
    return None


def model_modes(model):
    modes = ["txt2img"] if wf_spec(model.get("workflow")) else []
    return modes + [m for m, v in (model.get("workflows") or {}).items() if m not in modes and wf_spec(v)]


def workflow_for(model, mode):
    wfs = model.get("workflows") or {}
    if mode == "txt2img":
        return wf_spec(model.get("workflow")) or wf_spec(wfs.get("txt2img"))
    return wf_spec(wfs.get(mode))


def load_template(rel):
    path = (ROOT / rel).resolve()
    if ROOT not in path.parents:
        raise BenchError(f"Workflow {rel} is outside the data folder")
    if not path.is_file():
        raise BenchError(f"Workflow {rel} is missing")
    try:
        t = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise BenchError(f"Workflow {rel} is not valid JSON: {e}") from None
    if not isinstance(t, dict) or ("nodes" in t and "links" in t):
        raise BenchError(f"{rel} is in UI format. Please save it in ComfyUI via \"Export (API)\".")
    return t


# ================================================================ Variables

def norm_var(defn, shared, pid, name):
    if isinstance(defn, str) and defn.startswith("$"):
        key = defn[1:]
        if key not in shared:
            raise BenchError(f"{pid}: variable set ${key} is not defined")
        defn = shared[key]
    if isinstance(defn, dict):
        return [(str(k), v) for k, v in defn.items()]
    if isinstance(defn, list):
        return [(str(v), v) for v in defn]
    return [(str(defn), defn)]


def expand_variants(p, shared):
    """Returns a list of variants: {name: {"label": ..., "value": ...}}."""
    vars_ = p.get("vars") or {}
    if not vars_:
        return [{}]
    names = list(vars_)
    opts = [norm_var(vars_[n], shared, p["id"], n) for n in names]
    if any(not o for o in opts):
        raise BenchError(f"{p['id']}: empty variable list")
    mode = p.get("vary", "product")
    if mode == "product":
        combos = list(itertools.product(*opts))
    elif mode == "zip":
        if len({len(o) for o in opts}) > 1:
            raise BenchError(f"{p['id']}: vary: zip requires lists of equal length")
        combos = list(zip(*opts))
    elif mode == "single":
        base = [o[0] for o in opts]
        combos = [tuple(base)]
        for i, o in enumerate(opts):
            for alt in o[1:]:
                c = list(base)
                c[i] = alt
                combos.append(tuple(c))
    else:
        raise BenchError(f"{p['id']}: vary must be product, zip or single")
    return [{n: {"label": lab, "value": val} for n, (lab, val) in zip(names, c)} for c in combos]


def variant_key(variant):
    return "|".join(f"{n}={variant[n]['label']}" for n in sorted(variant))


def variant_label(variant):
    return " · ".join(f"{n}: {variant[n]['label']}" for n in variant)


def variant_slug(variant):
    if not variant:
        return "base"
    s = "_".join(f"{n}-{variant[n]['label']}" for n in sorted(variant))
    return re.sub(r"[^\w.-]+", "-", s).strip("-")[:80] or "v"


def render_text(text, variant):
    return PROMPT_VAR.sub(
        lambda m: str(variant[m.group(1)]["value"]) if m.group(1) in variant else m.group(0), text)


def parse_var_filter(items):
    out = {}
    for item in items or []:
        if "=" not in item:
            raise BenchError(f"--var expects name=value[,value] (got: {item})")
        k, v = item.split("=", 1)
        out.setdefault(k.strip(), set()).update(x.strip() for x in v.split(",") if x.strip())
    return out


def parse_sweep(items):
    """--sweep cfg=3,5,7  →  {"cfg": [{"label": "3", "value": 3}, …]} (values are typed)."""
    out = {}
    for item in items or []:
        if "=" not in item:
            raise BenchError(f"Sweep expects name=value,value (got: {item})")
        k, v = item.split("=", 1)
        if not re.fullmatch(r"\w+", k.strip()):
            raise BenchError(f"Sweep name \"{k}\": letters, digits and _ only")
        vals = [x.strip() for x in v.split(",") if x.strip()]
        if not vals:
            raise BenchError(f"Sweep {k}: no values")
        out[k.strip()] = [{"label": x, "value": yaml.safe_load(x)} for x in vals]
    return out


def parse_lora_combo(item):
    """Parse --lora-combo "style,detail:0.7" → (["style", "detail"], {"detail": 0.7})."""
    if not item:
        return [], {}
    parts = item.split(",")
    names = []
    strengths = {}
    for p in parts:
        if ":" in p:
            name, strength = p.split(":", 1)
            names.append(name.strip())
            strengths[name.strip()] = float(strength.strip())
        else:
            names.append(p.strip())
    return names, strengths


def parse_sweep_loras(item, available_loras):
    """Parse --sweep-loras all|pairs|singles."""
    item = item.strip().lower()
    lora_names = list(available_loras.keys())
    if not lora_names:
        return []
    if item == "all":
        # All non-empty subsets
        combos = []
        for r in range(1, len(lora_names) + 1):
            for combo in itertools.combinations(lora_names, r):
                combos.append(",".join(combo))
        return combos
    elif item == "pairs":
        return [",".join(c) for c in itertools.combinations(lora_names, 2)]
    elif item == "singles":
        return lora_names
    else:
        raise BenchError("sweep_loras must be all, pairs or singles")


BASE_COMBO = {"names": [], "strengths": {}, "triggers": {}, "combo_key": "none", "combo_label": "base"}


def expand_lora_combos(combo_strings, available_loras):
    """Expand combo strings into combo dicts for one model.

    Names that this model does not define are dropped (returned separately as
    skipped) instead of raising, so one run can cover models with different
    LoRA sets. A combo that loses all its names degrades to the base combo.
    """
    results, skipped, has_base = [], [], False
    for combo_str in combo_strings:
        names, strengths = parse_lora_combo(combo_str)
        known = []
        for n in names:
            if n in available_loras:
                known.append(n)
            elif n not in skipped:
                skipped.append(n)
        if not known:
            if not has_base:
                results.append(dict(BASE_COMBO))
                has_base = True
            continue
        # Build trigger dict for active LoRAs
        triggers = {n: available_loras[n].get("trigger", "") for n in known}
        # Build strength dict with defaults
        strength_dict = {}
        for n in known:
            strength_dict[n] = strengths.get(n, available_loras[n].get("default_strength", 0.8))
        # Keep explicit strengths in the key (file name), drop skipped names
        combo_key = ",".join(n if n not in strengths else f"{n}:{strengths[n]}" for n in known)
        results.append({
            "names": known,
            "strengths": strength_dict,
            "triggers": triggers,
            "combo_key": combo_key or "none",
            "combo_label": "+".join(known) if known else "base"
        })
    return results, skipped


def variant_matches(variant, vf):
    return all(n in variant and variant[n]["label"] in labels for n, labels in vf.items())


# ================================================================ Placeholders

def inject(obj, values, used):
    """Replace {{name}} in the workflow. If a placeholder stands alone in a field,
    the value is inserted with its type (a number stays a number)."""
    if isinstance(obj, dict):
        return {k: inject(v, values, used) for k, v in obj.items()}
    if isinstance(obj, list):
        return [inject(v, values, used) for v in obj]
    if isinstance(obj, str):
        whole = PLACEHOLDER.fullmatch(obj.strip())
        if whole:
            key = whole.group(1)
            if key not in values:
                raise KeyError(key)
            used.add(key)
            return values[key]

        def rep(m):
            key = m.group(1)
            if key not in values:
                raise KeyError(key)
            used.add(key)
            return str(values[key])

        return PLACEHOLDER.sub(rep, obj)
    return obj


def prepare_workflow(template, mapping, values, used):
    """Fill placeholders and apply the mapping (mapping sets node fields directly, even across links)."""
    wf = inject(template, values, used)
    if not mapping:
        return wf
    mapped = set()
    for key, targets in mapping.items():
        if key not in values:
            raise KeyError(key)
        for t in (targets if isinstance(targets, list) else [targets]):
            nid, _, inp = str(t).partition("/")
            if nid not in wf or not inp:
                raise BenchError(f"Mapping \"{key}\": {t} does not exist in the workflow")
            wf[nid].setdefault("inputs", {})[inp] = values[key]
            mapped.add(f"{nid}/{inp}")
        used.add(key)
    for nid, node in wf.items():   # keep ComfyUI's output organized, unless mapped explicitly
        if node.get("class_type") == "SaveImage" and f"{nid}/filename_prefix" not in mapped:
            node.setdefault("inputs", {})["filename_prefix"] = f"comfybench/{values['model_id']}/{values['prompt_id']}"
    return wf


def value_targets(template, mapping):
    """Which node inputs receive each value: {key: [(class_type, input), …]}.
    Covers {{placeholder}} fields and mapping targets; targets missing from the workflow are skipped."""
    out = {}

    def add(key, nid, inp):
        ct = (template.get(nid) or {}).get("class_type")
        if ct and (ct, inp) not in out.setdefault(key, []):
            out[key].append((ct, inp))

    for nid, node in template.items():
        if isinstance(node, dict):
            for inp, v in (node.get("inputs") or {}).items():
                m = PLACEHOLDER.fullmatch(v.strip()) if isinstance(v, str) else None
                if m:
                    add(m.group(1), nid, inp)
    for key, targets in (mapping or {}).items():
        for t in (targets if isinstance(targets, list) else [targets]):
            nid, _, inp = str(t).partition("/")
            if nid in template and inp:
                add(key, nid, inp)
    return {k: v for k, v in out.items() if v}


def inject_lora_nodes(wf, lora_combo, model_input_ref):
    """Dynamically inject LoRA loader nodes into the workflow.

    model_input_ref: (sampler_node_id, input_name) as returned by
    find_model_input_for_sampler. The existing model chain is kept:
    model source → LoRA1 → LoRA2 → ... → sampler
    """
    if not lora_combo or not lora_combo.get("names"):
        return wf, model_input_ref

    # Find the sampler node that uses the model input
    sampler_nid, sampler_input = model_input_ref
    inputs = (wf.get(sampler_nid) or {}).get("inputs") or {}
    source = inputs.get(sampler_input)
    if not isinstance(source, list) or len(source) != 2:
        return wf, model_input_ref   # no model link to chain onto
    try:
        # Start at the node the sampler currently takes its model from, not at
        # the sampler itself – a sampler outputs a LATENT, not a MODEL.
        current_model_ref = (str(source[0]), int(source[1]))
    except (TypeError, ValueError):
        return wf, model_input_ref
    new_node_id = max((int(k) for k in wf if k.isdigit()), default=0) + 1
    
    for lora_name in lora_combo["names"]:
        lora_info = lora_combo.get("lora_info", {}).get(lora_name, {})
        lora_file = lora_info.get("name", lora_name)
        strength = lora_combo["strengths"].get(lora_name, 0.8)
        
        # Create LoraLoaderModelOnly node
        lora_node = {
            "inputs": {
                "lora_name": lora_file,
                "strength_model": strength,
                "model": list(current_model_ref)
            },
            "class_type": "LoraLoaderModelOnly"
        }
        
        wf[str(new_node_id)] = lora_node
        current_model_ref = (str(new_node_id), 0)
        new_node_id += 1
    
    # Update the sampler to point at the last LoRA
    if sampler_nid in wf:
        wf[sampler_nid].setdefault("inputs", {})[sampler_input] = list(current_model_ref)
    
    return wf, current_model_ref


def find_model_input_for_sampler(wf):
    """Finds the model input of the first sampler in the workflow.
    Returns: (sampler_node_id, input_name) or None.
    """
    for nid, node in wf.items():
        ct = node.get("class_type", "")
        if "Sampler" in ct:
            inputs = node.get("inputs", {})
            if "model" in inputs and isinstance(inputs["model"], list):
                return nid, "model"
    return None


def build_values(cfg, model_id, model, p, variant, seed, lora_combo=None):
    values = {"negative": ""}
    values.update(cfg.get("defaults") or {})
    values.update(model.get("params") or {})
    values.update(p.get("params") or {})
    values.update((p.get("model_params") or {}).get(model_id) or {})
    for k, v in p.items():
        if k in ("width", "height") or IMAGE_KEY.match(k):
            values[k] = v
    for n, v in variant.items():
        values[n] = v["value"]
    
    # LoRA trigger words – every LoRA known to the config gets a value, so a
    # {lora_<name>_trigger} placeholder never leaks into the prompt when the
    # LoRA is inactive or the model does not define it at all.
    lora_triggers = {f"lora_{n}_trigger": ""
                     for m in (cfg.get("models") or {}).values()
                     for n in (m.get("loras") or {})}
    for lora_name, trigger in (lora_combo or {}).get("triggers", {}).items():
        lora_triggers[f"lora_{lora_name}_trigger"] = trigger or ""
    values.update(lora_triggers)
    
    # Create a combined variant dict for prompt rendering (includes LoRA triggers)
    render_variant = {**variant}
    for k, v in lora_triggers.items():
        render_variant[k] = {"label": v, "value": v}
    
    values["prompt"] = render_text(p["prompt"], render_variant)
    values["negative"] = render_text(p.get("negative", values.get("negative", "")), render_variant)
    
    values.update(seed=seed, model_id=model_id, prompt_id=p["id"], variant=variant_slug(variant))
    return values


def fingerprint(wf):
    return hashlib.sha1(json.dumps(wf, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


# ================================================================ Storage

class Store:
    """results/generations.jsonl (append-only) + results/ratings.json"""

    lock = threading.Lock()   # shared by all instances – the server and the run thread use separate Store objects

    def __init__(self, base):
        self.base = base
        self.gen_path = base / "generations.jsonl"
        self.rat_path = base / "ratings.json"

    def generations(self):
        if not self.gen_path.exists():
            return []
        out = []
        for line in self.gen_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return out

    def append(self, rec):
        with self.lock:
            self.base.mkdir(parents=True, exist_ok=True)
            with self.gen_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def ratings(self):
        if not self.rat_path.exists():
            return {}
        try:
            return json.loads(self.rat_path.read_text(encoding="utf-8"))
        except ValueError:
            return {}

    def delete_generation(self, gid):
        """Deletes a generation entry including its files and rating."""
        with self.lock:
            # 1. Rewrite generations (without this entry)
            gens = self.generations()
            target = None
            new_gens = []
            for g in gens:
                if g.get("id") == gid:
                    target = g
                else:
                    new_gens.append(g)
            if target is None:
                raise BenchError(f"Generation {gid} not found")

            # Delete files
            base = self.base
            for rel in target.get("files", []):
                try:
                    (base / rel).unlink(missing_ok=True)
                except OSError:
                    pass

            # Rewrite generations.jsonl
            tmp = self.gen_path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for g in new_gens:
                    f.write(json.dumps(g, ensure_ascii=False) + "\n")
            tmp.replace(self.gen_path)

            # 2. Delete the rating
            ratings = self.ratings()
            if gid in ratings:
                del ratings[gid]
                tmp = self.rat_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(ratings, indent=1, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.rat_path)

            return True

    def set_rating(self, gid, rating, note):
        with self.lock:
            data = self.ratings()
            entry = {}
            if rating:
                entry["rating"] = int(rating)
            if note and note.strip():
                entry["note"] = note
            if entry:
                entry["at"] = dt.datetime.now().isoformat(timespec="seconds")
                data[gid] = entry
            else:
                data.pop(gid, None)
            tmp = self.rat_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.rat_path)


class Inputs:
    """Input images: deterministic name (hash), copy for the gallery, upload to ComfyUI.
    Also supports references to earlier generations: gen:<generation_id>
    """

    def __init__(self, server, base, store=None):
        self.server = server
        self.base = base
        self.store = store
        self.info = {}
        self.uploaded = set()

    def prepare(self, rel):
        if rel not in self.info:
            # gen:<id> → reference an earlier generation
            if rel.startswith("gen:") and self.store:
                gid = rel[4:]
                gens = self.store.generations()
                gen = next((g for g in gens if g.get("id") == gid), None)
                if not gen:
                    raise FileNotFoundError(f"Generation {gid} not found")
                if not gen.get("files"):
                    raise FileNotFoundError(f"Generation {gid} has no image files")
                # Take the first file of the generation
                rel_path = gen["files"][0]
                p = (self.base / rel_path).resolve()
                if not p.is_file():
                    raise FileNotFoundError(f"File of generation {gid} not found: {rel_path}")
            else:
                # Normal: look for the file in inputs/
                for b in (ROOT / "inputs", ROOT):
                    p = (b / rel).resolve()
                    if ROOT in p.parents and p.is_file():
                        break
                else:
                    raise FileNotFoundError(f"Input image \"{rel}\" not found (is it in inputs/? or use gen:<id>)")
            data = p.read_bytes()
            name = f"{hashlib.sha1(data).hexdigest()[:10]}_{p.name}"
            self.info[rel] = {"ref": f"comfybench/{name}", "copy": f"_inputs/{name}", "path": p, "name": name}
        return self.info[rel]

    def ensure(self, rel):
        i = self.prepare(rel)
        dest = self.base / i["copy"]
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(i["path"].read_bytes())
        if rel in self.uploaded:
            return
        r = requests.post(
            f"{self.server}/upload/image",
            files={"image": (i["name"], i["path"].read_bytes(),
                             mimetypes.guess_type(i["name"])[0] or "application/octet-stream")},
            data={"type": "input", "subfolder": "comfybench", "overwrite": "true"},
            timeout=120,
        )
        r.raise_for_status()
        j = r.json()
        actual = f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]
        if actual != i["ref"]:
            raise RuntimeError(f"Upload landed as {actual} instead of {i['ref']}")
        self.uploaded.add(rel)


# ================================================================ ComfyUI-API

def submit(server, workflow, client_id):
    r = requests.post(f"{server}/prompt", json={"prompt": workflow, "client_id": client_id}, timeout=30)
    if r.status_code != 200:
        try:
            detail = json.dumps(r.json(), indent=2, ensure_ascii=False)
        except ValueError:
            detail = r.text
        raise RuntimeError(f"ComfyUI rejected the workflow ({r.status_code}):\n{detail}")
    return r.json()["prompt_id"]


def wait_for(server, prompt_id, timeout, abort=None):
    start = time.time()
    while True:
        if abort is not None and abort.is_set():
            raise RuntimeError("Aborted")
        r = requests.get(f"{server}/history/{prompt_id}", timeout=30)
        r.raise_for_status()
        hist = r.json()
        if prompt_id in hist:
            entry = hist[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                raise RuntimeError(f"Execution error: {json.dumps(msgs, ensure_ascii=False)[:2000]}")
            if status.get("completed", True):
                return entry
        if time.time() - start > timeout:
            cancel(server, prompt_id)
            raise TimeoutError(f"No result after {timeout}s")
        time.sleep(1)


def cancel(server, prompt_id):
    """Remove our job from ComfyUI's queue and stop it if it is running."""
    for path, body in (("/queue", {"delete": [prompt_id]}), ("/interrupt", {"prompt_id": prompt_id})):
        try:
            requests.post(f"{server}{path}", json=body, timeout=5)
        except requests.RequestException:
            pass


def collect_images(entry):
    found = []
    for node_out in entry.get("outputs", {}).values():
        found.extend(node_out.get("images", []))
    saved = [i for i in found if i.get("type") == "output"]
    return saved or found  # Fallback: PreviewImage (temp)


def collect_texts(entry):
    """Text outputs (e.g. PreviewAny with the LLM-extended prompt)."""
    out = []
    for node_out in entry.get("outputs", {}).values():
        for t in node_out.get("text", []) or []:
            if isinstance(t, str) and t.strip():
                out.append(t)
    return out


def download(server, img, dest):
    params = {"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")}
    r = requests.get(f"{server}/view", params=params, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)


OBJECT_INFO_CACHE = {}   # (server, class_type) → node info from /object_info


def object_info(server, cls):
    key = (server, cls)
    if key not in OBJECT_INFO_CACHE:
        r = requests.get(f"{server}/object_info/{cls}", timeout=(5, 30))
        r.raise_for_status()
        OBJECT_INFO_CACHE[key] = (r.json() or {}).get(cls) or {}   # unknown class (custom node missing) → {}
    return OBJECT_INFO_CACHE[key]


def choice_options(info, inp):
    """Option list of a choice input (["a", "b"] or the newer ["COMBO", {"options": […]}]), else None."""
    ins = info.get("input") or {}
    spec = (ins.get("required") or {}).get(inp) or (ins.get("optional") or {}).get(inp)
    if not isinstance(spec, list) or not spec:
        return None
    if isinstance(spec[0], list):
        return [str(x) for x in spec[0]]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict) and isinstance(spec[1].get("options"), list):
        return [str(x) for x in spec[1]["options"]]
    return None


def comfy_options(cfg, refresh=False):
    """Values ComfyUI accepts per model value (choice inputs such as checkpoint, sampler) plus the LoRA files."""
    server = resolve_server(cfg)
    if refresh:
        OBJECT_INFO_CACHE.clear()
    models = {}
    try:
        for mid, model in (cfg.get("models") or {}).items():
            opts = models[mid] = {}
            for mode in model_modes(model):
                spec = workflow_for(model, mode)
                try:
                    template = load_template(spec["file"])
                except BenchError:
                    continue
                for key, targets in value_targets(template, spec["map"]).items():
                    for ct, inp in targets:
                        choices = choice_options(object_info(server, ct), inp)
                        if choices:
                            lst = opts.setdefault(key, [])
                            lst.extend(c for c in choices if c not in lst)
        loras = choice_options(object_info(server, "LoraLoader"), "lora_name") or []
    except (requests.ConnectionError, requests.Timeout):
        return {"ok": False, "error": f"ComfyUI not reachable at {server}"}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "error": f"ComfyUI at {server}: {e}"}
    return {"ok": True, "models": models, "loras": loras}


# ================================================================ run

def parse_list(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def resolve_server(cfg):
    return (os.environ.get("COMFYUI_URL") or cfg.get("server", "http://127.0.0.1:8188")).rstrip("/")


def execute(cfg, prompts, opts, emit, stop=None, abort=None):
    """Core of a run – used by the CLI and the Generate tab of the gallery.

    opts: models, prompts, categories (lists or None), var (list of "name=a,b"), sweep (list of "name=a,b"),
          force, dry_run, plan (count only, send or write nothing).
          lora_combo: list of combo strings, sweep_loras: "all|pairs|singles|combo1,combo2"
    emit(event, **data): plan, warn, skip, start, ok, fail, dry, abort, end
    """
    server = resolve_server(cfg)
    timeout = cfg.get("timeout", 900)
    base = out_dir(cfg)
    store = Store(base)
    inputs = Inputs(server, base, store)
    plan, dry_run, force = opts.get("plan"), opts.get("dry_run"), opts.get("force")

    model_filter = opts.get("models") or None
    prompt_filter = opts.get("prompts") or None
    cat_filter = opts.get("categories") or None
    var_filter = parse_var_filter(opts.get("var"))
    sweep = parse_sweep(opts.get("sweep"))
    sweep_by_model = opts.get("sweep_by_model") or {}

    # LoRA combo handling
    lora_combo_strings = opts.get("lora_combo") or []
    sweep_loras = opts.get("sweep_loras")

    unknown = set(model_filter or []) - set(cfg["models"])
    if unknown:
        raise BenchError(f"Unknown models: {', '.join(sorted(unknown))}")
    unknown = set(prompt_filter or []) - {p["id"] for p in prompts}
    if unknown:
        raise BenchError(f"Unknown prompts: {', '.join(sorted(unknown))}")

    if model_filter:
        models = {k: v for k, v in cfg["models"].items() if k in model_filter}
    else:
        models = {k: v for k, v in cfg["models"].items() if v.get("enabled", True) is not False}
    sel_prompts = [p for p in prompts
                   if (not prompt_filter or p["id"] in prompt_filter)
                   and (not cat_filter or p.get("category") in cat_filter)]

    done_fps = {
        g["fingerprint"] for g in store.generations()
        if g.get("fingerprint") and g.get("status") == "ok" and g.get("files") and all((base / f).exists() for f in g["files"])
    }

    templates, warned, jobs = {}, set(), []
    for mid, model in models.items():
        # Sweep values valid for this model (sweep_by_model overrides the global list per key)
        msweep, empty = dict(sweep), []
        for item in sweep_by_model.get(mid) or []:
            k, _, v = item.partition("=")
            if v.strip():
                msweep.update(parse_sweep([item]))
            else:
                empty.append(k.strip())
        if empty:
            emit("warn", msg=f"{mid}: no valid values for sweep {', '.join(empty)} – skipped")
            continue
        sweep_combos = [dict(zip(msweep, c)) for c in itertools.product(*msweep.values())] if msweep else [{}]

        # Get available LoRAs for this model
        available_loras = model.get("loras") or {}
        
        # Parse LoRA combos for this model
        model_lora_combos = []
        if lora_combo_strings:
            model_lora_combos, skipped_loras = expand_lora_combos(lora_combo_strings, available_loras)
        elif sweep_loras:
            sweep_combo_strings = parse_sweep_loras(sweep_loras, available_loras)
            model_lora_combos, skipped_loras = expand_lora_combos(sweep_combo_strings, available_loras)
            # Fallback to base run if no LoRAs available
            if not model_lora_combos:
                model_lora_combos = [dict(BASE_COMBO)]
        else:
            # No LoRA combos - single run with no LoRAs
            skipped_loras = []
            model_lora_combos = [dict(BASE_COMBO)]
        for ln in skipped_loras:
            msg = f"{mid}: LoRA \"{ln}\" not defined for this model – skipped"
            if (mid, msg) not in warned:
                warned.add((mid, msg))
                emit("warn", msg=msg)
        
        # Add lora_info to each combo for dynamic injection
        for combo in model_lora_combos:
            combo["lora_info"] = {n: available_loras[n] for n in combo["names"]}
        
        for p in sel_prompts:
            mode = p["_mode"]
            spec = workflow_for(model, mode)
            if not spec:
                if (mid, mode) not in warned:
                    emit("warn", msg=f"{mid}: no workflow for {mode} – skipping matching prompts")
                    warned.add((mid, mode))
                continue
            wf_rel = spec["file"]
            if wf_rel not in templates:
                try:
                    templates[wf_rel] = load_template(wf_rel)
                except BenchError as e:
                    emit("warn", msg=f"{mid}: {e} – skipped")
                    templates[wf_rel] = None
            if templates[wf_rel] is None:
                continue
            
            # Find model input for sampler (for dynamic LoRA injection)
            model_input_ref = find_model_input_for_sampler(templates[wf_rel])
            combos = model_lora_combos
            if not model_input_ref and any(c["names"] for c in combos):
                if (mid, wf_rel) not in warned:
                    warned.add((mid, wf_rel))
                    emit("warn", msg=f"{mid}: no sampler model input found for dynamic LoRA injection – LoRAs are ignored")
                combos = [c for c in combos if not c["names"]] or [dict(BASE_COMBO)]

            # Image keys the workflow takes, via mapping or {{placeholder}}
            mapped_img_keys = [k for k in spec["map"] if IMAGE_KEY.match(k)]
            mapped_img_keys += [k for k in PLACEHOLDER.findall(json.dumps(templates[wf_rel]))
                                if IMAGE_KEY.match(k)]

            for variant in p["_variants"]:
                if var_filter and not variant_matches(variant, var_filter):
                    continue
                for sw in sweep_combos:
                    full = {**variant, **sw}
                    for seed in p.get("seeds", cfg.get("seeds", [42])):
                        for lora_combo in combos:
                            jobs.append((mid, model, mode, (templates[wf_rel], spec["map"]), p, full, seed, mapped_img_keys, lora_combo, model_input_ref))

    emit("plan", total=len(jobs))
    client_id = str(uuid.uuid4())
    done = skipped = failed = idle_skipped = 0
    stopped = False

    for n, (mid, model, mode, template, p, variant, seed, mapped_img_keys, lora_combo, model_input_ref) in enumerate(jobs, 1):
        if stop is not None and stop.is_set():
            stopped = True
            break
        vlabel = variant_label(variant)
        lora_label = f" · LoRA: {lora_combo['combo_label']}" if lora_combo.get("names") else ""
        label = f"{mid} · {p['id']}{' · ' + vlabel if vlabel else ''}{lora_label} · seed {seed}"
        base_rec = {"model": mid, "prompt": p["id"], "variant": {k: v["label"] for k, v in variant.items()},
                    "variant_key": variant_key(variant), "seed": seed, "mode": mode,
                    "lora_combo": lora_combo.get("combo_key", "none"), "lora_names": lora_combo.get("names", [])}

        def fail(msg, record=True):
            nonlocal failed
            failed += 1
            emit("fail", n=n, label=label, msg=str(msg))
            if record and not dry_run and not plan:
                store.append({**base_rec, "id": uuid.uuid4().hex[:10], "status": "error",
                              "error": str(msg)[:800], "files": [], "at": now()})

        values = build_values(cfg, mid, model, p, variant, seed, lora_combo)
        # Remove image keys from prompt that aren't mapped in workflow
        for k in list(values.keys()):
            if IMAGE_KEY.match(k) and k not in mapped_img_keys:
                del values[k]
        # Override image fields from UI selection
        ui_images = opts.get("images", {}).get(p["id"], {})
        for img_key, img_val in ui_images.items():
            if img_val:
                values[img_key] = img_val
        input_src, input_copy = {}, {}
        try:
            for k in sorted(values):
                if IMAGE_KEY.match(k) and isinstance(values[k], str) and values[k]:
                    info = inputs.prepare(values[k])
                    input_src[k] = values[k]
                    input_copy[k] = info["copy"]
                    values[k] = info["ref"]
            used = set()
            wf = prepare_workflow(template[0], template[1], values, used)
            # Inject LoRA nodes dynamically if needed (for placeholder workflows)
            if lora_combo.get("names") and model_input_ref:
                wf, _ = inject_lora_nodes(wf, lora_combo, model_input_ref)
        except KeyError as e:
            fail(f"Placeholder {{{{{e.args[0]}}}}} has no value (set a parameter or default value)")
            continue
        except (FileNotFoundError, BenchError) as e:
            fail(e)
            continue

        idle = [k for k in sweep if k not in used and f"{{{k}}}" not in p["prompt"]]
        if idle:
            idle_skipped += 1
            if (mid, tuple(idle)) not in warned:
                emit("warn", msg=f"{mid}: sweep {', '.join(idle)} is not used in the workflow – skipped")
                warned.add((mid, tuple(idle)))
            continue

        fp = fingerprint(wf)
        if fp in done_fps and not force:
            skipped += 1
            emit("skip", n=n, label=label)
            continue

        if plan:
            done += 1
            continue

        if dry_run:
            d = base / "_dryrun"
            d.mkdir(parents=True, exist_ok=True)
            lora_suffix = f"__{lora_combo['combo_key']}" if lora_combo.get("names") else ""
            (d / f"{mid}__{p['id']}__{variant_slug(variant)}{lora_suffix}__s{seed}.json").write_text(
                json.dumps(wf, indent=2, ensure_ascii=False), encoding="utf-8")
            unused = [v for v in variant if v not in used and f"{{{v}}}" not in p["prompt"]
                      and f"{{{v}}}" not in p.get("negative", "")]
            emit("dry", n=n, label=label, hint=f"Variable never used: {', '.join(unused)}" if unused else "")
            done += 1
            continue

        emit("start", n=n, label=label)
        t0 = time.time()
        try:
            for rel in input_src.values():
                inputs.ensure(rel)
            pid = submit(server, wf, client_id)
            emit("queued", prompt_id=pid)
            entry = wait_for(server, pid, timeout, abort)
            images = collect_images(entry)
            texts = collect_texts(entry)
            if not images:
                raise RuntimeError("Workflow produced no image (SaveImage/PreviewImage missing?)")
            gid = uuid.uuid4().hex[:10]
            ddir = base / mid / p["id"]
            ddir.mkdir(parents=True, exist_ok=True)
            files = []
            for i, img in enumerate(images):
                ext = Path(img["filename"]).suffix or ".png"
                dest = ddir / f"{variant_slug(variant)}__s{seed}__{gid}{'_' + str(i) if i else ''}{ext}"
                download(server, img, dest)
                files.append(dest.relative_to(base).as_posix())
        except requests.ConnectionError:
            emit("abort", msg=f"ComfyUI at {server} is not reachable.")
            stopped = True
            break
        except Exception as e:  # noqa: BLE001
            if stop is not None and stop.is_set():   # aborted by the user – no error entry
                emit("abort", msg="Aborted.")
                stopped = True
                break
            fail(e)
            continue

        secs = round(time.time() - t0, 1)
        rec = {
            **base_rec, "id": gid, "status": "ok", "fingerprint": fp,
            "text": values["prompt"], "negative": values.get("negative", ""),
            "final_text": next((t for t in texts if t.strip() != values["prompt"].strip()), ""),
            "values": {k: values[k] for k in sorted(used) if k not in INTERNAL_KEYS},
            "inputs": input_copy, "input_src": input_src,
            "files": files, "seconds": secs, "at": now(),
        }
        store.append(rec)
        done_fps.add(fp)
        done += 1
        emit("ok", n=n, label=label, secs=secs, rec=rec)

    summary = {"total": len(jobs), "done": done, "skipped": skipped, "failed": failed,
               "idle": idle_skipped, "stopped": stopped}
    if not dry_run and not plan and done:
        write_static(cfg, prompts, store)
    emit("end", **summary)
    return summary


def cli_emit(total_holder):
    def emit(ev, **d):
        tot = total_holder.get("total", "?")
        if ev == "plan":
            total_holder["total"] = d["total"]
            print(f"{d['total']} Jobs")
        elif ev == "warn":
            print(f"! {d['msg']}")
        elif ev == "start":
            print(f"[{d['n']}/{tot}] {d['label']}", end="", flush=True)
        elif ev == "ok":
            print(f"  ✓ {d['secs']}s")
        elif ev == "fail":
            print(f"\n[{d['n']}/{tot}] {d['label']}  ✗ {d['msg']}")
        elif ev == "dry":
            print(f"[{d['n']}/{tot}] {d['label']}  ✓ filled" + (f"  ⚠ {d['hint']}" if d["hint"] else ""))
        elif ev == "abort":
            print(f"\n✗ {d['msg']}")
        elif ev == "end":
            print(f"\nDone: {d['done']} generated, {d['skipped']} skipped, {d['failed']} failed")
    return emit


def cmd_run(args):
    cfg, prompts = load_all(args.config, args.prompts_file)
    opts = {"models": parse_list(args.models), "prompts": parse_list(args.prompts),
            "categories": parse_list(args.category), "var": args.var, "sweep": args.sweep,
            "lora_combo": args.lora_combo, "sweep_loras": args.sweep_loras,
            "force": args.force, "dry_run": args.dry_run}
    summary = execute(cfg, prompts, opts, cli_emit({}))
    if not args.dry_run and summary["done"]:
        print("View it: python bench.py serve")


class Runner:
    """One run at a time, in the background of the gallery server."""

    def __init__(self, config_path, prompts_path):
        self.config_path, self.prompts_path = config_path, prompts_path
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.abort = threading.Event()
        self.prompt_id = None
        self.thread = None
        self.log = deque(maxlen=400)
        self.recent = deque(maxlen=12)
        self.st = {"state": "idle"}
        self.server = None

    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def status(self):
        with self.lock:
            st = dict(self.st)
            st["log"] = list(self.log)
            st["recent"] = list(self.recent)
        if st.get("state") in ("running", "stopping") and st.get("secs"):
            remaining = st["total"] - st["done"] - st["skipped"] - st["failed"] - st.get("idle", 0)
            st["eta"] = round(remaining * sum(st["secs"]) / len(st["secs"]))
        st.pop("secs", None)
        return st

    def _log(self, level, msg):
        self.log.append({"t": dt.datetime.now().strftime("%H:%M:%S"), "level": level, "msg": msg})

    def emit(self, ev, **d):
        with self.lock:
            st = self.st
            if ev == "plan":
                st["total"] = d["total"]
                self._log("info", f"{d['total']} jobs planned")
            elif ev == "queued":
                self.prompt_id = d["prompt_id"]
            elif ev == "warn":
                self._log("warn", d["msg"])
            elif ev == "skip":
                st["skipped"] += 1
            elif ev == "start":
                st["current"] = d["label"]
                st["current_since"] = time.time()
            elif ev == "ok":
                st["done"] += 1
                st["secs"] = (st.get("secs") or [])[-20:] + [d["secs"]]
                st["current"] = ""
                r = d["rec"]
                self.recent.appendleft({"id": r["id"], "file": r["files"][0], "model": r["model"],
                                        "prompt": r["prompt"], "variant": r["variant"], "seed": r["seed"]})
                self._log("ok", f"{d['label']} · {d['secs']}s")
            elif ev == "dry":
                st["done"] += 1
                self._log("ok", f"{d['label']} filled" + (f" – {d['hint']}" if d["hint"] else ""))
            elif ev == "fail":
                st["failed"] += 1
                st["current"] = ""
                self._log("err", f"{d['label']}: {d['msg'][:400]}")
            elif ev == "abort":
                self._log("err", d["msg"])
                st["msg"] = d["msg"]
                st["abort_error"] = not self.stop.is_set()
            elif ev == "end":
                st["idle"] = d["idle"]

    def start(self, opts):
        with self.lock:
            if self.running():
                raise BenchError("A run is already in progress.")
            cfg, prompts = load_all(self.config_path, self.prompts_path)
            self.server = resolve_server(cfg)
            self.stop.clear()
            self.abort.clear()
            self.prompt_id = None
            self.log.clear()
            self.recent.clear()
            self.st = {"state": "running", "started": now(), "total": 0, "done": 0, "skipped": 0, "failed": 0,
                       "idle": 0, "current": "", "msg": "", "dry_run": bool(opts.get("dry_run")),
                       "opts": {k: v for k, v in opts.items() if k != "plan"}}
            self.thread = threading.Thread(target=self._run, args=(cfg, prompts, opts), daemon=True)
            self.thread.start()

    def _run(self, cfg, prompts, opts):
        try:
            summary = execute(cfg, prompts, opts, self.emit, self.stop, self.abort)
            with self.lock:
                self.st["state"] = ("error" if self.st.get("abort_error") else "stopped") if summary["stopped"] else "done"
                self._log("info", f"Done: {summary['done']} generated, {summary['skipped']} skipped, "
                                  f"{summary['failed']} failed")
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.st["state"] = "error"
                self.st["msg"] = str(e)
                self._log("err", str(e))
        finally:
            with self.lock:
                self.st["finished"] = now()
                self.st["current"] = ""

    def request_stop(self, hard):
        if not self.running():
            return
        self.stop.set()
        with self.lock:
            self.st["state"] = "stopping"
            self._log("info", "Aborting …" if hard else "Stopping after the current image …")
        if hard:
            self.abort.set()
            if self.server and self.prompt_id:
                cancel(self.server, self.prompt_id)


def plan_run(config_path, prompts_path, opts):
    cfg, prompts = load_all(config_path, prompts_path)
    out = {"warnings": [], "errors": []}

    def emit(ev, **d):
        if ev == "warn":
            out["warnings"].append(d["msg"])
        elif ev == "fail":
            out["errors"].append(f"{d['label']}: {d['msg']}")
    summary = execute(cfg, prompts, {**opts, "plan": True, "dry_run": False}, emit)
    out.update(total=summary["total"], new=summary["done"], existing=summary["skipped"],
               failed=summary["failed"], idle=summary["idle"])
    out["errors"] = out["errors"][:20]
    return out


# ================================================================ Data for the interface

def ui_data(cfg, prompts, store, static):
    gens = store.generations()
    bench = [g for g in gens if g.get("mode") != "import"]
    seeds = sorted({g["seed"] for g in bench if isinstance(g.get("seed"), int)} | set(cfg.get("seeds", [42])))
    for p in prompts:
        seeds = sorted(set(seeds) | set(p.get("seeds", [])))
    # Variants from prompts.yaml plus everything that was actually generated (e.g. via --sweep)
    seen = {}
    for g in bench:
        seen.setdefault(g["prompt"], {}).setdefault(g.get("variant_key", ""), g.get("variant") or {})

    def variants_for(p):
        out = [{"key": variant_key(v), "label": variant_label(v),
                "variant": {k: x["label"] for k, x in v.items()}} for v in p["_variants"]]
        known = {v["key"] for v in out}
        for key, var in seen.get(p["id"], {}).items():
            if key not in known:
                out.append({"key": key, "label": " · ".join(f"{k}: {l}" for k, l in var.items()),
                            "variant": var, "extra": True})
        return out

    return {
        "static": static,
        "generated": now(),
        "models": [{"id": k, "label": v.get("label", k), "notes": v.get("notes", ""), "modes": model_modes(v),
                    "enabled": v.get("enabled", True) is not False, "keys": model_keys(v),
                    "workflows": v.get("workflows") or {},
                    "loras": v.get("loras") or {}}
                   for k, v in cfg["models"].items()],
        "prompts": [{
            "id": p["id"], "category": p.get("category", ""), "prompt": p["prompt"],
            "negative": p.get("negative", ""), "check": p.get("check", ""), "mode": p["_mode"],
            "variants": variants_for(p),
            "image": p.get("image"), "image2": p.get("image2"),
            "width": p.get("width"), "height": p.get("height"),
        } for p in prompts],
        "seeds": seeds,
        "generations": gens,
        "ratings": store.ratings(),
    }


def model_keys(model):
    """Values a model uses in any of its workflows (for sweep suggestions)."""
    keys = set()
    for mode in model_modes(model):
        spec = workflow_for(model, mode)
        keys.update(spec["map"])
        try:
            keys.update(PLACEHOLDER.findall(json.dumps(load_template(spec["file"]))))
        except BenchError:
            pass
    return sorted(keys - {"model_id", "prompt_id", "variant"})


def run_opts(data):
    """Translate a selection from the UI into run options."""
    def strlist(x):
        return [str(v) for v in x] if isinstance(x, list) else None
    sweep = data.get("sweep") or {}
    if isinstance(sweep, dict):
        sweep = [f"{k}={v}" for k, v in sweep.items() if str(k).strip() and str(v).strip()]
    # Per-model values for sweeps over choice fields (an empty value skips the model)
    by_model = data.get("sweep_by_model")
    sweep_by_model = {str(m): [f"{k}={v}" for k, v in d.items() if str(k).strip()]
                      for m, d in by_model.items() if isinstance(d, dict)} if isinstance(by_model, dict) else {}
    lora_combo = data.get("lora_combo")
    if isinstance(lora_combo, list):
        lora_combo = [str(v) for v in lora_combo if str(v).strip()]
    elif isinstance(lora_combo, str):
        lora_combo = [lora_combo] if lora_combo.strip() else []
    else:
        lora_combo = None
    sweep_loras = data.get("sweep_loras")
    if isinstance(sweep_loras, str):
        sweep_loras = sweep_loras.strip().lower() or None
    else:
        sweep_loras = None
    return {"models": strlist(data.get("models")), "prompts": strlist(data.get("prompts")),
            "categories": None, "var": None, "sweep": sweep, "sweep_by_model": sweep_by_model,
            "lora_combo": lora_combo, "sweep_loras": sweep_loras,
            "force": bool(data.get("force")), "dry_run": bool(data.get("dry_run")),
            "images": data.get("images") or {}}


def template_html():
    return (CODE / "report_template.html").read_text(encoding="utf-8")


def write_static(cfg, prompts, store):
    base = out_dir(cfg)
    base.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(ui_data(cfg, prompts, store, True), ensure_ascii=False).replace("</", "<\\/")
    path = base / "index.html"
    path.write_text(template_html().replace(DATA_MARK, payload), encoding="utf-8")
    return path


def cmd_report(args):
    cfg, prompts = load_all(args.config, args.prompts_file)
    print(f"Static report: {write_static(cfg, prompts, Store(out_dir(cfg)))}")


# ================================================================ import

def png_info(path):
    """Read text chunks (tEXt/zTXt/iTXt) and size from a PNG – no extra packages."""
    import struct
    import zlib
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    pos, texts, size = 8, {}, None
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        try:
            if ctype == b"IHDR":
                size = struct.unpack(">II", chunk[:8])
            elif ctype == b"tEXt":
                k, v = chunk.split(b"\0", 1)
                texts[k.decode("latin-1")] = v.decode("latin-1")
            elif ctype == b"zTXt":
                k, rest = chunk.split(b"\0", 1)
                texts[k.decode("latin-1")] = zlib.decompress(rest[1:]).decode("latin-1")
            elif ctype == b"iTXt":
                k, rest = chunk.split(b"\0", 1)
                comp = rest[0]
                _, _, rest = rest[2:].split(b"\0", 2)
                texts[k.decode("latin-1")] = (zlib.decompress(rest) if comp else rest).decode("utf-8")
            elif ctype == b"IEND":
                break
        except (ValueError, zlib.error):
            continue
    return texts, size


TEXT_KEYS = ("text", "prompt", "t5xxl", "text_g", "clip_l", "string", "value", "text_l")
MODEL_KEYS = ("ckpt_name", "unet_name", "model_name", "diffusion_model")


def find_text_target(api, ref, depth=0):
    """Follows a conditioning/text link backwards. Returns (node_id, input) of the original text."""
    if depth > 12 or not isinstance(ref, list) or not ref:
        return None
    nid = str(ref[0])
    node = api.get(nid)
    if not node:
        return None
    ins = node.get("inputs", {})
    if "switch" in ins and ("on_true" in ins or "on_false" in ins):   # If/Else switch: take the active branch
        sw = ins.get("switch")
        active = sw not in (False, "false", "False", 0, None)
        return find_text_target(api, ins.get("on_true") if active else ins.get("on_false"), depth + 1)
    for k in TEXT_KEYS + ("source", "conditioning", "conditioning_1", "positive", "cond"):
        v = ins.get(k)
        if isinstance(v, str) and k in TEXT_KEYS:
            return nid, k
        if isinstance(v, list):
            t = find_text_target(api, v, depth + 1)
            if t:
                return t
    return None


def resolve_text(api, ref):
    t = find_text_target(api, ref)
    if not t:
        return None
    v = api[t[0]]["inputs"][t[1]]
    return v if v.strip() else None


def extract_meta(api):
    pos = neg = seed = model = None
    params = {}
    for node in api.values():
        ins = node.get("inputs", {})
        ct = node.get("class_type", "")
        if pos is None and isinstance(ins.get("positive"), list):
            pos = resolve_text(api, ins["positive"])
            if isinstance(ins.get("negative"), list):
                neg = resolve_text(api, ins["negative"])
        if pos is None and "Guider" in ct and isinstance(ins.get("conditioning"), list):
            pos = resolve_text(api, ins["conditioning"])
        for k in ("seed", "noise_seed"):
            if seed is None and isinstance(ins.get(k), int):
                seed = ins[k]
        for k in MODEL_KEYS:
            if model is None and isinstance(ins.get(k), str):
                model = ins[k]
        for k in ("steps", "cfg", "guidance", "sampler_name", "scheduler", "denoise"):
            if k not in params and isinstance(ins.get(k), (int, float, str)):
                params[k] = ins[k]
    if pos is None:  # Fallback: longest text of a text encoder
        cands = [n["inputs"].get("text") for n in api.values()
                 if "TextEncode" in n.get("class_type", "") and isinstance(n.get("inputs", {}).get("text"), str)]
        pos = max(cands, key=len) if cands else None
    return pos, neg, seed, model, params


def cmd_import(args):
    cfg, _ = load_all(args.config, args.prompts_file)
    base = out_dir(cfg)
    store = Store(base)
    gens = store.generations()
    known = {g.get("fingerprint") for g in gens}
    seen = {(g.get("source"), g.get("at")) for g in gens if g.get("mode") == "import"}
    src = Path(args.folder).expanduser()
    # Do not import benchmark images (SaveImage prefix comfybench/) twice
    files = [src] if src.is_file() else sorted(
        p for p in src.rglob("*.png") if not {"comfybench", "_dryrun"} & set(p.relative_to(src).parts[:-1]))
    if args.since:
        cutoff = dt.datetime.fromisoformat(args.since).timestamp()
        files = [f for f in files if f.stat().st_mtime >= cutoff]
    added = dup = nometa = 0
    for f in files:
        mtime = dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
        if (str(f), mtime) in seen:
            dup += 1
            continue
        data = f.read_bytes()
        fp = "file:" + hashlib.sha1(data).hexdigest()[:16]
        if fp in known:
            dup += 1
            continue
        texts, size = png_info(f)
        try:
            api = json.loads((texts or {}).get("prompt", ""))
        except ValueError:
            api = None
        if not isinstance(api, dict):
            nometa += 1
            continue
        pos, neg, seed, model, params = extract_meta(api)
        model_id = "import:" + (Path(model).stem if model else "unknown")
        gid = uuid.uuid4().hex[:10]
        dest = base / "_import" / f"{f.stem[:60]}__{gid}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        values = dict(params)
        if model:
            values["checkpoint"] = model
        if size:
            values["width"], values["height"] = size
        store.append({
            "id": gid, "status": "ok", "fingerprint": fp, "mode": "import",
            "model": model_id, "prompt": "_import", "variant": {}, "variant_key": "",
            "seed": seed, "text": pos or "", "negative": neg or "", "values": values,
            "inputs": {}, "input_src": {}, "files": [dest.relative_to(base).as_posix()],
            "seconds": None, "at": mtime, "source": str(f),
        })
        known.add(fp)
        added += 1
    print(f"{added} imported, {dup} already present, {nometa} skipped without ComfyUI metadata")


# ================================================================ Setup (mapping, checking, saving)

STANDARD_KEYS = ["prompt", "negative", "seed", "width", "height", "steps", "cfg", "sampler",
                 "scheduler", "denoise", "guidance", "image"]
BUILTIN_KEYS = {"prompt", "negative", "seed", "width", "height", "model_id", "prompt_id", "variant"}


def analyze_workflow(api):
    """List nodes and suggest a mapping."""
    if not isinstance(api, dict) or ("nodes" in api and "links" in api):
        raise BenchError("This is the UI format. Please save it in ComfyUI via \"Export (API)\".")
    has_image = any(n.get("class_type") == "LoadImage" for n in api.values() if isinstance(n, dict))
    sugg = {}

    def add(key, nid, inp):
        v = api[nid].get("inputs", {}).get(inp)
        if isinstance(v, str) and PLACEHOLDER.fullmatch(v.strip()):
            return  # already {{…}} in the workflow
        lst = sugg.setdefault(key, [])
        if f"{nid}/{inp}" not in lst:
            lst.append(f"{nid}/{inp}")

    sampler_keys = {"seed": "seed", "noise_seed": "seed", "steps": "steps", "cfg": "cfg",
                    "sampler_name": "sampler", "scheduler": "scheduler", "guidance": "guidance"}
    for nid, n in api.items():
        if not isinstance(n, dict):
            continue
        ins, ct = n.get("inputs", {}), n.get("class_type", "")
        for k, key in sampler_keys.items():
            if k in ins and not isinstance(ins[k], list):
                add(key, nid, k)
        if has_image and "denoise" in ins and not isinstance(ins["denoise"], list):
            add("denoise", nid, "denoise")
        if ct == "LoadImage" and "image" in ins:
            add("image", nid, "image")
        if "Latent" in ct and "width" in ins and "height" in ins:
            add("width", nid, "width")
            add("height", nid, "height")
        for link_key, key in (("positive", "prompt"), ("negative", "negative")):
            t = find_text_target(api, ins.get(link_key)) if isinstance(ins.get(link_key), list) else None
            if t:
                add(key, *t)
        if "Guider" in ct and isinstance(ins.get("conditioning"), list):
            t = find_text_target(api, ins["conditioning"])
            if t:
                add("prompt", *t)
    for k in ("prompt", "negative"):
        if k in sugg:
            sugg[k] = sugg[k][:1]
    if sugg.get("negative") and sugg.get("negative") == sugg.get("prompt"):
        sugg.pop("negative")
    values = {}   # current values of the suggested fields (for model parameters)
    for key, targets in sugg.items():
        nid, _, inp = targets[0].partition("/")
        v = api[nid]["inputs"].get(inp)
        if not isinstance(v, list):
            values[key] = v
    nodes = [{"id": nid, "class_type": n.get("class_type", ""), "title": (n.get("_meta") or {}).get("title", ""),
              "inputs": [{"name": k, "value": v, "link": isinstance(v, list)} for k, v in n.get("inputs", {}).items()]}
             for nid, n in api.items() if isinstance(n, dict)]
    return {
        "nodes": nodes, "suggest": sugg, "values": values,
        "placeholders": sorted(set(PLACEHOLDER.findall(json.dumps(api)))),
        "has_output": any(n.get("class_type") in ("SaveImage", "SaveImageAdvanced", "PreviewImage") for n in api.values() if isinstance(n, dict)),
    }


def check_setup(cfg, prompts, options=None):
    """Trial-fill every model workflow (like --dry-run, but only one job per mode).
    With options from comfy_options, configured values ComfyUI does not offer are reported."""
    out = []
    shared_seed = (cfg.get("seeds") or [42])[0]
    for mid, model in (cfg.get("models") or {}).items():
        if model.get("enabled", True) is False:
            continue
        modes = model_modes(model)
        if not modes:
            out.append({"model": mid, "mode": "–", "ok": False, "msg": "no workflow configured"})
        for mode in modes:
            spec = workflow_for(model, mode)
            sample = next((p for p in prompts if p["_mode"] == mode), None)
            p = sample or {"id": "_check", "prompt": "test prompt", "_variants": [{}]}
            if mode != "txt2img" and not sample:
                p = {**p, "image": "check.png"}
            try:
                template = load_template(spec["file"])
                values = build_values(cfg, mid, model, p, p["_variants"][0], shared_seed)
                for k in list(values):
                    if IMAGE_KEY.match(k) and isinstance(values[k], str):
                        values[k] = f"comfybench/{values[k]}"
                used = set()
                prepare_workflow(template, spec["map"], values, used)
                gaps = [k for k in ("prompt", "seed") if k not in used]
                msg = "ok" + (f" · warning: {', '.join(gaps)} not applied" if gaps else "")
                out.append({"model": mid, "mode": mode, "ok": not gaps, "warn": bool(gaps), "msg": msg,
                            "used": sorted(used - {"model_id", "prompt_id", "variant"}),
                            "sample": p["id"] if sample else None})
            except KeyError as e:
                out.append({"model": mid, "mode": mode, "ok": False,
                            "msg": f"\"{e.args[0]}\" is mapped but has no value (set a parameter or default value)"})
            except BenchError as e:
                out.append({"model": mid, "mode": mode, "ok": False, "msg": str(e)})
    if options and options.get("ok"):
        for mid, model in (cfg.get("models") or {}).items():
            if model.get("enabled", True) is False:
                continue
            values = {**(cfg.get("defaults") or {}), **(model.get("params") or {})}
            missing = [f"{k}: {values[k]} not in ComfyUI" for k, choices in (options["models"].get(mid) or {}).items()
                       if values.get(k) is not None and str(values[k]) not in choices]
            missing += [f"LoRA {n}: {info['name']} not in ComfyUI" for n, info in (model.get("loras") or {}).items()
                        if isinstance(info, dict) and info.get("name") and info["name"] not in options["loras"]]
            for msg in missing:
                out.append({"model": mid, "mode": "ComfyUI", "ok": False, "warn": True, "msg": msg})
    return out


def cmd_check(args):
    cfg, prompts = load_all(args.config, args.prompts_file)
    options = comfy_options(cfg)
    if not options["ok"]:
        print(f"! {options['error']} – model files not checked")
    bad = 0
    for r in check_setup(cfg, prompts, options):
        mark = "✓" if r["ok"] else ("!" if r.get("warn") else "✗")
        bad += not r["ok"]
        extra = f"  [{', '.join(r['used'])}]" if r.get("used") else ""
        print(f"{mark} {r['model']} · {r['mode']}: {r['msg']}{extra}")
    sys.exit(1 if bad else 0)


class _Dumper(yaml.SafeDumper):
    pass


def _str_rep(dumper, value):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


def _list_rep(dumper, value):
    # short lists of simple values inline ([42, 1337]), everything else as a block
    simple = all(isinstance(x, (int, float, bool)) or (isinstance(x, str) and "\n" not in x) for x in value)
    flow = simple and len(json.dumps(value, ensure_ascii=False)) <= 90
    return dumper.represent_sequence("tag:yaml.org,2002:seq", value, flow_style=flow)


_Dumper.add_representer(str, _str_rep)
_Dumper.add_representer(list, _list_rep)
MODEL_KEY_ORDER = ["label", "notes", "enabled", "workflow", "workflows", "params"]


def _ordered(d, order):
    return {**{k: d[k] for k in order if k in d}, **{k: v for k, v in d.items() if k not in order}}


def ordered_config(cfg):
    out = _ordered(cfg, ["server", "output_dir", "timeout", "seeds", "defaults", "models"])
    models = {}
    for mid, m in (cfg.get("models") or {}).items():
        m = _ordered(m, MODEL_KEY_ORDER)
        specs = [("workflow", m.get("workflow"))] + [(k, v) for k, v in (m.get("workflows") or {}).items()]
        for _, spec in specs:
            if isinstance(spec, dict) and isinstance(spec.get("map"), dict):
                spec["map"] = _ordered(spec["map"], STANDARD_KEYS)
        models[mid] = m
    out["models"] = models
    return out

CONFIG_HEADER = "# ComfyBench – Configuration. Editable in the gallery under \"Setup\" or by hand (see README.md).\n\n"
PROMPTS_HEADER = "# ComfyBench – Prompt set. Editable in the gallery under \"Setup\" or by hand (see README.md).\n\n"
PROMPT_KEY_ORDER = ["id", "category", "prompt", "negative", "check", "image", "width", "height",
                    "seeds", "mode", "vars", "vary", "params", "model_params"]


def write_yaml(path, data, header):
    path = Path(path)
    if path.exists():
        bdir = ROOT / ".backups"
        bdir.mkdir(exist_ok=True)
        shutil.copy2(path, bdir / f"{path.stem}.{dt.datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
        old = sorted(bdir.glob(f"{path.stem}.*{path.suffix}"))
        for f in old[:-20]:
            f.unlink()
    text = header + yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                              width=100, default_flow_style=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def validate_config(cfg):
    if not isinstance(cfg, dict) or not isinstance(cfg.get("models"), dict):
        raise BenchError("config needs a models section")
    for mid, m in cfg["models"].items():
        if not SLUG.match(mid):
            raise BenchError(f"Model ID \"{mid}\": letters, digits, - _ . only")
        if not isinstance(m, dict):
            raise BenchError(f"Model {mid}: invalid entry")
        specs = [("txt2img", m.get("workflow"))] + list((m.get("workflows") or {}).items())
        for mode, spec in specs:
            sp = wf_spec(spec)
            if spec and not sp:
                raise BenchError(f"Model {mid} · {mode}: workflow spec is incomplete")
            if sp and not (ROOT / sp["file"]).is_file():
                raise BenchError(f"Model {mid} · {mode}: file {sp['file']} does not exist")
    seeds = cfg.get("seeds")
    if seeds is not None and not (isinstance(seeds, list) and all(isinstance(x, int) for x in seeds) and seeds):
        raise BenchError("seeds must be a list of integers")


def validate_prompts(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("prompts"), list):
        raise BenchError("prompts is missing")
    shared = doc.get("vars") or {}
    ids = set()
    for p in doc["prompts"]:
        pid = p.get("id", "")
        if not SLUG.match(str(pid)):
            raise BenchError(f"Prompt ID \"{pid}\": letters, digits, - _ . only")
        if pid in ids:
            raise BenchError(f"Duplicate prompt ID {pid}")
        ids.add(pid)
        if not str(p.get("prompt", "")).strip():
            raise BenchError(f"{pid}: prompt text is missing")
        expand_variants(p, shared)


def list_files(sub, exts):
    d = ROOT / sub
    if not d.is_dir():
        return []
    return sorted(f.relative_to(d).as_posix() for f in d.rglob("*") if f.is_file() and f.suffix.lower() in exts)


def setup_data(config_path, prompts_path):
    return {
        "config": load_yaml(config_path),
        "prompts": load_yaml(prompts_path),
        "files": {"workflows": ["workflows/" + f for f in list_files("workflows", {".json"})],
                  "inputs": list_files("inputs", {".png", ".jpg", ".jpeg", ".webp"})},
        "standard_keys": STANDARD_KEYS,
        "builtin_keys": sorted(BUILTIN_KEYS),
    }


def safe_name(name, exts, default):
    name = re.sub(r"[^\w.-]+", "-", Path(str(name)).name).strip("-.") or "file"
    if Path(name).suffix.lower() not in exts:
        name += default
    return name


# ================================================================ serve

def cmd_serve(args):
    runner = Runner(args.config, args.prompts_file)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def results(self):
            """Current output dir and store, re-read from config.yaml per request
            (the gallery follows output_dir changes in Setup without a restart)."""
            b = out_dir(load_yaml(args.config))
            return b, Store(b)

        def send(self, code, body, ctype="application/json; charset=utf-8", cache=False):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=31536000, immutable" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            if path in ("/", "/index.html"):
                return self.send(200, template_html(), "text/html; charset=utf-8")
            if path == "/api/data":
                try:
                    c, prompts = load_all(args.config, args.prompts_file)
                except BenchError as e:
                    return self.send(500, json.dumps({"error": str(e)}, ensure_ascii=False))
                _, store = self.results()
                return self.send(200, json.dumps(ui_data(c, prompts, store, False), ensure_ascii=False))
            if path == "/api/run/status":
                return self.send(200, json.dumps(runner.status(), ensure_ascii=False))
            if path == "/api/setup":
                return self.send(200, json.dumps(setup_data(args.config, args.prompts_file), ensure_ascii=False))
            if path == "/api/comfy/options":
                refresh = "refresh=1" in (urlparse(self.path).query or "")
                return self.send(200, json.dumps(comfy_options(load_yaml(args.config), refresh), ensure_ascii=False))
            if path.startswith("/inputs/"):
                f = (ROOT / "inputs" / path[len("/inputs/"):]).resolve()
                if (ROOT / "inputs").resolve() in f.parents and f.is_file():
                    return self.send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            if path.startswith("/files/"):
                b, _store = self.results()
                f = (b / path[len("/files/"):]).resolve()
                if b in f.parents and f.is_file():
                    return self.send(200, f.read_bytes(),
                                     mimetypes.guess_type(f.name)[0] or "application/octet-stream", cache=True)
            return self.send(404, '{"error":"not found"}')

        def body(self):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                raise BenchError("Content-Type must be application/json")
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

        def ok(self, obj=None):
            return self.send(200, json.dumps(obj if obj is not None else {"ok": True}, ensure_ascii=False))

        def err(self, msg, code=400):
            return self.send(code, json.dumps({"error": str(msg)}, ensure_ascii=False))

        def do_PUT(self):
            path = urlparse(self.path).path
            try:
                data = self.body()
                if path == "/api/setup/config":
                    validate_config(data)
                    write_yaml(args.config, ordered_config(data), CONFIG_HEADER)
                    return self.ok()
                if path == "/api/setup/prompts":
                    doc = {}
                    if data.get("vars"):
                        doc["vars"] = data["vars"]
                    doc["prompts"] = [_ordered(p, PROMPT_KEY_ORDER) for p in data.get("prompts", [])]
                    validate_prompts(doc)
                    write_yaml(args.prompts_file, doc, PROMPTS_HEADER)
                    return self.ok()
            except (BenchError, ValueError) as e:
                return self.err(e)
            return self.err("not found", 404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path.startswith("/api/"):
                try:
                    data = self.body()
                    if path == "/api/rating":
                        _, store = self.results()
                        store.set_rating(str(data["id"]), data.get("rating") or 0, data.get("note") or "")
                        return self.ok()
                    if path == "/api/workflow/analyze":
                        api = data.get("workflow")
                        if api is None:
                            api = load_template(data["file"]) if data.get("file") else None
                        return self.ok(analyze_workflow(api))
                    if path == "/api/run/plan":
                        return self.ok(plan_run(args.config, args.prompts_file, run_opts(data)))
                    if path == "/api/run/start":
                        opts = run_opts(data)
                        if not opts["models"] or not opts["prompts"]:
                            return self.err("Select at least one model and one prompt.")
                        runner.start(opts)
                        return self.ok(runner.status())
                    if path == "/api/run/stop":
                        runner.request_stop(bool(data.get("hard")))
                        return self.ok(runner.status())
                    if path == "/api/check":
                        c, prompts = load_all(args.config, args.prompts_file)
                        return self.ok({"results": check_setup(c, prompts, comfy_options(c))})
                    if path == "/api/upload":
                        kind = data.get("kind")
                        if kind == "workflow":
                            wf = data.get("workflow")
                            analyze_workflow(wf)   # validates the format
                            name = safe_name(data.get("name", "workflow.json"), {".json"}, ".json")
                            dest = ROOT / "workflows" / name
                            if dest.exists() and json.loads(dest.read_text(encoding="utf-8")) != wf and not data.get("overwrite"):
                                return self.err(f"workflows/{name} already exists with different content", 409)
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_text(json.dumps(wf, indent=2, ensure_ascii=False), encoding="utf-8")
                            return self.ok({"path": f"workflows/{name}"})
                        if kind == "input":
                            name = safe_name(data.get("name", "image.png"), {".png", ".jpg", ".jpeg", ".webp"}, ".png")
                            dest = ROOT / "inputs" / name
                            raw = base64.b64decode(data["data"].split(",")[-1])
                            if dest.exists() and dest.read_bytes() != raw and not data.get("overwrite"):
                                return self.err(f"inputs/{name} already exists with different content", 409)
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(raw)
                            return self.ok({"path": name})
                        return self.err("unknown upload")
                    if path == "/api/delete":
                        try:
                            gid = str(data.get("id", ""))
                            if not gid:
                                return self.err("Missing ID")
                            _, store = self.results()
                            store.delete_generation(gid)
                        except (ValueError, KeyError, BenchError) as e:
                            return self.err(e)
                        return self.ok()
                except (BenchError, ValueError, KeyError) as e:
                    return self.err(e)
                return self.err("not found", 404)
            return self.send(404, '{"error":"not found"}')

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Gallery running at http://{args.host}:{args.port}  (Ctrl+C quits)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


# ================================================================ main

def main():
    ap = argparse.ArgumentParser(description="ComfyUI model benchmark with gallery")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--prompts-file", default=str(ROOT / "prompts.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="generate images")
    r.add_argument("--models", help="comma-separated model IDs")
    r.add_argument("--prompts", help="comma-separated prompt IDs")
    r.add_argument("--category", help="comma-separated categories")
    r.add_argument("--var", action="append", metavar="NAME=VALUE[,VALUE]",
                   help="only these variable values (repeatable)")
    r.add_argument("--sweep", action="append", metavar="NAME=VALUE,VALUE",
                   help="extra axis for all jobs, e.g. cfg=3,5,7 or sampler=euler,dpmpp_2m")
    r.add_argument("--lora-combo", action="append", metavar="NAME[,NAME:STRENGTH]",
                   help="LoRA combination for this run, e.g. \"style\" or \"style,detail:0.7\" (repeatable)")
    r.add_argument("--sweep-loras", choices=["all", "pairs", "singles"],
                   help="test all LoRA combinations: all=all subsets, pairs=only pairs, singles=only single LoRAs")
    r.add_argument("--force", action="store_true", help="regenerate results that already exist")
    r.add_argument("--dry-run", action="store_true", help="only fill workflows, send nothing")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("serve", help="gallery and comparison in the browser")
    s.add_argument("--host", default=os.environ.get("COMFYBENCH_HOST", "127.0.0.1"),
                   help="0.0.0.0 to allow access from the network")
    s.add_argument("--port", type=int, default=int(os.environ.get("COMFYBENCH_PORT", 8765)))
    s.set_defaults(func=cmd_serve)

    im = sub.add_parser("import", help="bring own ComfyUI images (PNG with metadata) into the gallery")
    im.add_argument("folder", help="folder or file, e.g. ComfyUI/output")
    im.add_argument("--since", help="only files from this date on, e.g. 2026-10-01")
    im.set_defaults(func=cmd_import)

    ck = sub.add_parser("check", help="trial-fill all model workflows and report problems")
    ck.set_defaults(func=cmd_check)

    rp = sub.add_parser("report", help="static copy (read-only) to results/index.html")
    rp.set_defaults(func=cmd_report)

    args = ap.parse_args()
    try:
        args.func(args)
    except BenchError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
