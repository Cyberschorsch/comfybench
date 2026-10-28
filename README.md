# ComfyBench

Benchmark image models in [ComfyUI](https://github.com/comfyanonymous/ComfyUI) against a fixed prompt set, keep every result in a permanent gallery, and compare them side by side.

- **Bring your own workflows**: each model uses its own ComfyUI workflow per mode (txt2img, img2img, inpaint, …). Your exported files stay untouched; ComfyBench fills in prompt, seed, size and parameters at runtime.
- **Systematic variations**: prompt variables, parameter sweeps (`cfg`, `sampler`, checkpoints, …) and LoRA combinations.
- **Browser UI**: set up models and prompts, start and monitor runs, compare, rate and annotate results.
- **Nothing gets lost**: results are versioned, never overwritten, and already generated images are skipped.

## Quick start

Requirements: a running ComfyUI instance and Python 3 (tested with 3.12), or Docker.

```bash
pip install -r requirements.txt
python bench.py serve        # UI at http://127.0.0.1:8765
```

Then in the UI: **Setup** to add models and prompts, **Generate** to run, **Compare** and **Gallery** to look at the results. Everything is also available on the [command line](#command-line).

The example config runs `sdxl-base` with the bundled workflows (ComfyUI needs `sd_xl_base_1.0.safetensors`). The disabled `moss-image` entry only illustrates the mapping syntax; its workflow file is not included.

### Docker

```bash
cp .env.example .env              # set PUID/PGID and COMFYUI_URL
docker compose up -d --build      # UI at http://localhost:8765
```

On first start the container fills `./data` with the example `config.yaml`, `prompts.yaml`, `workflows/`, `inputs/` and a copy of this README. Edit your files there; results go to `data/results/`. Existing files are never overwritten, so rebuilding the image keeps your data.

CLI commands run inside the container: `docker compose exec comfybench bench run --dry-run`.

To import your own ComfyUI images, uncomment the `/comfy-output` volume in `docker-compose.yml` (mounted read-only).

### Connecting to ComfyUI

The address comes from `server` in `config.yaml` (default `http://127.0.0.1:8188`). The environment variable `COMFYUI_URL` overrides it; Docker sets it from `.env`.

| ComfyUI runs … | `COMFYUI_URL` |
|---|---|
| on the same machine | `http://host.docker.internal:8188` (start ComfyUI with `--listen`) |
| on another machine | `http://<ip>:8188` |
| in the same Compose project | `http://comfyui:8188` |

Without Docker, set `COMFYBENCH_HOME=/path/to/data` to keep your data separate from the code.

## Adding a model

1. Build and test the workflow in ComfyUI, then save it with **Workflow → Export (API)**.
2. In **Setup → Models**, add a model and upload the file as the workflow for a mode (txt2img, img2img, …).
3. ComfyBench analyzes the graph and suggests a mapping: seed, steps, cfg, sampler, scheduler and guidance from the sampler, width and height from the latent node, image and denoise for img2img. It finds the prompt text by tracing back from the sampler through switches, primitives and guiders. Review the suggestions; one value can feed several fields (e.g. `seed` into the sampler and an LLM node), and custom names such as `enhance` for a switch can be added.
4. **Save**. This writes `config.yaml` and checks all workflows.

Mapped fields are overwritten at runtime, even where the original has a link, so a resolution selector chain does not need to be removed. The resulting config looks like this:

```yaml
my-model:
  label: My Model
  workflow:
    file: workflows/my-model-t2i.json
    map:
      prompt: ['18:17/value']                        # node id / input name
      seed: ['18:7/seed', '18:11/sampling_mode.seed']
      width: ['18:6/width']
      enhance: ['18:16/switch']
  workflows:
    img2img: workflows/my-model-i2i.json             # further modes
  params: {steps: 50, sampler: er_sde, enhance: true, negative: "blurry"}
```

`params` hold model defaults; a `negative` here applies whenever a prompt has none of its own.

**Placeholders instead of mapping**: you can also write placeholders directly into the exported JSON, or mix both. A placeholder that fills the whole field keeps its type (numbers stay numbers). See the SDXL examples in `workflows/`.

| Field | Placeholder |
|---|---|
| positive / negative prompt text | `"{{prompt}}"` / `"{{negative}}"` |
| `seed` / `noise_seed` | `"{{seed}}"` |
| latent `width`, `height` | `"{{width}}"`, `"{{height}}"` |
| LoadImage `image`, sampler `denoise` (img2img) | `"{{image}}"`, `"{{denoise}}"` |
| anything else (steps, cfg, checkpoint, …) | `"{{steps}}"` etc., value from `params` |
| SaveImage `filename_prefix` (optional) | `"bench/{{model_id}}/{{prompt_id}}"` |

For mapped workflows, `filename_prefix` is set to `comfybench/<model>/<prompt>` automatically. Text outputs of a workflow (e.g. `PreviewAny` showing an LLM-enhanced prompt) are saved with the image and shown in the full view.

**Model files**: ComfyBench asks ComfyUI (`/object_info`) which values a node accepts. Checkpoints, LoRAs, samplers, schedulers and so on become select lists in Setup and Generate, unknown values are flagged, and **Variants from files …** turns each selected checkpoint into a model of its own. Use **↻ Refresh lists** after downloading new models.

Verify a setup with `python bench.py check` (fills every workflow once and reports missing values and files ComfyUI does not offer) or `python bench.py run --models <id> --dry-run` (writes the filled workflows to `results/_dryrun/`).

## Prompts

`prompts.yaml` is the prompt library. Edit it in **Setup → Prompts** or by hand. The header of the example file documents every field.

```yaml
vars:                              # shared value lists
  emotions: {laughing: "laughing heartily", angry: "furious, clenched jaw"}

prompts:
  - id: portrait-emotion
    category: Variables
    prompt: "Portrait of a {age}-year-old woman {emotion}, soft light"
    vars:
      emotion: $emotions           # label: text, or a plain list
      age: [18, 30, 75]
    vary: single
    check: Expression believable?  # what to look for when rating
```

- **`vary`**: `product` (default, all combinations), `zip` (lists pairwise) or `single` (first values are the base, each variable is varied on its own).
- A variable named like a workflow value (`denoise`, `cfg`, `steps`, …) controls that value directly, e.g. for a denoise series.
- **img2img**: put the image into `inputs/` and set `image: file.png`. The prompt then uses each model's `img2img` workflow; models without one are skipped. Other modes work the same way (`mode: inpaint` with `workflows: {inpaint: …}`).
- When saving from the UI, comments in the YAML files are replaced by a short header and a backup goes to `.backups/` (last 20).

## Sweeps and LoRAs

**Sweeps** add axes to a run without touching `prompts.yaml`: `--sweep cfg=3,5,7`, or the **Sweeps** section in Generate. Models whose workflow does not use the value are skipped for that sweep.

**LoRAs** are defined per model in `config.yaml` or under **Setup → Models → LoRAs**:

```yaml
loras:
  style: {name: loras/style.safetensors, default_strength: 0.8, trigger: "in the style of"}
```

ComfyBench inserts one `LoraLoaderModelOnly` node per active LoRA in front of the sampler's model input; the workflow file stays unchanged. In Generate (or on the CLI) you pick the base run without LoRA, explicit combinations (`style,detail:0.7`), or a sweep over single LoRAs, pairs or all combinations. In prompts, `{lora_<name>_trigger}` becomes the trigger word when that LoRA is active and an empty string otherwise.

## Generating and comparing

- **Generate**: choose models, prompts or categories, sweeps and LoRAs. Before starting you see how many images are new or already exist, a time estimate and warnings. The run executes in the server, so it continues when you close the browser. You can stop after the current image or abort immediately; restarting skips finished images.
- **Compare**: pick the column axis (models, seeds, a variable or a LoRA combination), filter by seed, category and text. Average ratings appear per column.
- **Gallery**: every image ever generated, filterable and sortable by date or rating.
- **Ratings and notes** are stored in `results/ratings.json`.
- **Versions**: when prompt, workflow or parameters change, the next run adds a new version of the same cell instead of overwriting it.
- **"as YAML"** in the full view copies an image as a ready `prompts.yaml` entry, which brings good finds back into the test set.

Every PNG still contains its full workflow: drag it into ComfyUI to open it.

## Command line

```bash
python bench.py serve [--host 0.0.0.0] [--port 8765]   # UI; 0.0.0.0 for network access
python bench.py run [options]                          # generate
python bench.py check                                  # validate all model workflows
python bench.py import ~/ComfyUI/output --since 2026-10-01
python bench.py report                                 # static read-only copy: results/index.html
```

`run` options:

| Option | Effect |
|---|---|
| `--models a,b` / `--prompts x,y` / `--category Text` | limit the run |
| `--var emotion=angry,laughing` | only these variable values (and only prompts that have the variable) |
| `--sweep cfg=3,5,7` | extra axis, repeatable |
| `--lora-combo "style,detail:0.7"` | LoRA combination, repeatable |
| `--sweep-loras all\|pairs\|singles` | all LoRA combinations of that kind |
| `--force` | generate again as an additional version |
| `--dry-run` | fill workflows only, send nothing |

`import` reads PNGs with ComfyUI metadata (prompt, negative, seed, model, sampler values) into the gallery as "own image". Benchmark images and files imported before are skipped, so it is safe to run repeatedly.

Global options `--config` and `--prompts-file` point to other files.

## Data layout

```
config.yaml               # server, seeds, defaults, models
prompts.yaml              # prompt library
workflows/                # exported ComfyUI workflows (API format)
inputs/                   # input images for img2img
results/
  generations.jsonl       # every generation, append-only: parameters, texts, duration, errors
  ratings.json            # ratings and notes
  _inputs/  _import/      # copies of input images, imported images
  <model>/<prompt>/<variant>__s<seed>__<id>.png
```

## License

[GPL-3.0](LICENSE)
