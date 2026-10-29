import {html} from "cb/vendor/htm-preact.js";
import {state, update, loadComfyOptions, comfyOpts, comfyLoras} from "cb/store.js";
import {toast} from "cb/components/toast.js";
import {uiConfirm, uiPrompt} from "cb/components/dialog.js";
import {OptSelect} from "cb/components/common.js";
import {nameRule, RE_NAME, RE_ID, clone} from "cb/util.js";
import {Field, KvTable} from "cb/views/setup/field.js";
import {specOf, setSpec, modesOf, curModel, markDirty, uploadWorkflow, applySuggestions} from "cb/views/setup/logic.js";
import {Mapping, ensureAnalysis} from "cb/views/setup/mapping.js";

// ------------------------------------------------------------------ model list (:1208-1215)
async function addModel(){
  const su = state.setup;
  const id = await uiPrompt("Model ID", {placeholder: "e.g. flux-dev", ok: "Add model",
    validate: nameRule(RE_ID, "Letters, digits, - _ .", v => su.cfg.models[v])});
  if(!id) return;
  su.cfg.models[id] = {label: id, params: {}};
  su.model = id; su.mode = "txt2img"; markDirty("config");
}
async function delModel(){
  const su = state.setup;
  if(!await uiConfirm("It is removed from the config. Generated images stay in the gallery.",
      {title: `Delete model ${su.model}?`, ok: "Delete model", danger: true})) return;
  delete su.cfg.models[su.model];
  su.model = Object.keys(su.cfg.models)[0] || null;
  markDirty("config");
}

export function Models(){
  const su = state.setup, m = su.cfg.models[su.model];
  return html`<div class="su-cols">
    <div class="su-list">
      ${Object.keys(su.cfg.models).map(id => {
        const mm = su.cfg.models[id];
        return html`<button class=${su.model === id ? "on" : ""} onClick=${() => {
          su.model = id; su.mode = "txt2img"; update();
        }}>${mm.label || id}${mm.enabled === false ? " ⏸" : ""}
          <small>${id} · ${modesOf(mm).join(", ") || "no workflow"}</small></button>`;
      })}
      <button onClick=${addModel}>＋ Model</button>
    </div>
    <div class="su-edit">${m ? html`<${ModelEdit} id=${su.model} m=${m} />`
      : html`<p class="hint">No model yet. Create one and upload a workflow for it (saved in ComfyUI via "Export (API)").</p>`}</div>
  </div>`;
}

// ------------------------------------------------------------------ workflow select (:1219-1224, :1646-1653)
async function wfChange(mode, e){
  const su = state.setup, m = curModel(), el = e.currentTarget;
  let file = el.value;
  if(file === "__upload"){
    file = await uploadWorkflow();
    if(!file){ el.value = (specOf(m, mode) || {}).file || ""; return; }
  }
  const sp = specOf(m, mode) || {file: "", map: {}};
  sp.file = file; setSpec(m, mode, sp); su.mode = mode; markDirty("config");
  if(!file) return;
  const an = await ensureAnalysis();
  if(an && !an.error && !(an.placeholders || []).length && applySuggestions(m, mode, true)){
    markDirty("config"); su.info = "Mapping suggestions applied – please check briefly."; update();
  }
}

async function addMode(){
  const su = state.setup, m = curModel();
  const mode = await uiPrompt("Mode", {value: "img2img", placeholder: "e.g. img2img, inpaint", ok: "Add mode",
    validate: nameRule(RE_NAME, "Letters, digits and _")});
  if(!mode) return;
  if(!specOf(m, mode)){
    if(mode === "txt2img") m.workflow = {file: "", map: {}};
    else { m.workflows = m.workflows || {}; m.workflows[mode] = {file: "", map: {}}; }
  }
  su.mode = mode; markDirty("config");
}
async function delMode(mode){
  if(!await uiConfirm("Its workflow and input mapping are removed from this model.",
      {title: `Remove mode ${mode}?`, ok: "Remove", danger: true})) return;
  setSpec(curModel(), mode, null);
  markDirty("config");
}
function toggleEnabled(m, e){
  if(e.currentTarget.checked) delete m.enabled; else m.enabled = false;
  markDirty("config");
}

// ------------------------------------------------------------------ ComfyUI banner (:1249-1253)
function ComfyBanner(){
  const co = state.comfy.data;
  if(!co) return html`<p class="hint">Loading option lists from ComfyUI …</p>`;
  if(!co.ok) return html`<div class="warnline">${co.error} – no option lists, values are free text.
    <button onClick=${() => coRefresh()}>↻ Retry</button></div>`;
  return html`<p class="hint">Fields with a list come from ComfyUI.
    <button title="Reload the lists, e.g. after downloading new models" onClick=${() => coRefresh()}>↻ Refresh lists</button></p>`;
}
async function coRefresh(){
  const d = await loadComfyOptions(true);
  toast(d.ok ? "Lists reloaded from ComfyUI" : d.error);
}

// ------------------------------------------------------------------ LoRAs (:1254-1263)
function Loras({id, m}){
  const files = comfyLoras();
  const rows = Object.entries(m.loras || {}).map(([n, info]) => {
    info = info || {};
    const P = f => ["models", id, "loras", n, f];
    return html`<tr key=${n}><td class="k">${n}</td><td><div class="row">
      ${files
        ? html`<${OptSelect} value=${info.name} options=${files} onChange=${val => {
            m.loras[n].name = val; markDirty("config");
          }} style="flex:2;min-width:160px" />`
        : html`<${Field} root="cfg" path=${P("name")} placeholder="file, e.g. style.safetensors" style="flex:2;min-width:160px" />`}
      <${Field} root="cfg" path=${P("default_strength")} type="scalar" inputmode="decimal"
        placeholder="strength 0.8" style="width:100px" />
      <${Field} root="cfg" path=${P("trigger")} placeholder="trigger word (optional)" style="flex:1;min-width:120px" />
      </div></td>
      <td style="width:30px"><button class="x" onClick=${() => delLora(n)}>✕</button></td></tr>`;
  });
  return html`<table class="kv">${rows}</table>
    <button style="margin-top:6px" onClick=${addLora}>＋ LoRA</button>`;
}
async function addLora(){
  const m = curModel();
  const n = await uiPrompt("Short name of the LoRA", {placeholder: "e.g. style, detail", ok: "Add LoRA",
    validate: nameRule(RE_NAME, "Letters, digits and _", v => m.loras && m.loras[v])});
  if(!n) return;
  m.loras = m.loras || {};
  m.loras[n] = {name: "", default_strength: 0.8};
  markDirty("config");
}
function delLora(n){
  const m = curModel();
  delete m.loras[n];
  if(!Object.keys(m.loras).length) delete m.loras;
  markDirty("config");
}

// ------------------------------------------------------------------ variants from files (:1265-1277, :1716-1736)
function VarFilesPanel({id, m}){
  const su = state.setup, d = su.varDlg;
  const keys = Object.keys((state.comfy.data && state.comfy.data.ok && state.comfy.data.models[id]) || {});
  const q = d.q.trim().toLowerCase();
  const shown = (comfyOpts(id, d.key) || []).filter(o => !q || o.toLowerCase().includes(q));
  const setSel = sel => { d.sel = sel; update(); };
  return html`<div class="su-vd"><h3>Variants from files</h3>
    <p class="hint" style="margin-top:0">Creates one model per file: a copy of <b>${m.label || id}</b> with the same workflows, mapping, parameters and LoRAs – only this value differs. Saved with "Save".</p>
    <div class="row"><select value=${d.key} onChange=${e => { d.key = e.currentTarget.value; d.sel = []; update(); }}>
        ${keys.map(k => html`<option value=${k} selected=${k === d.key}>${k}</option>`)}
      </select>
      <input type="search" placeholder="Search files …" value=${d.q} style="flex:1;min-width:160px"
        onInput=${e => { d.q = e.currentTarget.value; update(); }} />
      <button onClick=${() => setSel([...new Set([...d.sel, ...shown])])}>all</button>
      <button onClick=${() => setSel(d.sel.filter(x => !shown.includes(x)))}>none</button></div>
    <div class="su-vd-list">${shown.length ? shown.map(o => html`<label class="rn-item">
        <input type="checkbox" checked=${d.sel.includes(o)} onChange=${e => setSel(e.currentTarget.checked
          ? [...new Set([...d.sel, o])] : d.sel.filter(x => x !== o))} /><span>${o}</span></label>`)
      : html`<p class="hint">No matching files.</p>`}</div>
    <div class="row" style="justify-content:flex-end">
      <button class="btn-p" disabled=${!d.sel.length} onClick=${() => createVariants(m)}>Create ${d.sel.length} model${d.sel.length === 1 ? "" : "s"}</button>
      <button onClick=${() => { su.varDlg = null; update(); }}>Cancel</button></div></div>`;
}
function createVariants(m){
  const su = state.setup, d = su.varDlg;
  const stem = f => f.split(/[\\/]/).pop().replace(/\.[^.]+$/, "");
  const made = [];
  for(const f of d.sel){
    const base = stem(f).toLowerCase().replace(/[^a-z0-9.]+/g, "-").replace(/^[-.]+|[-.]+$/g, "") || "model";
    let nid = base, n = 2; while(su.cfg.models[nid]) nid = `${base}-${n++}`;
    const c = clone(m); c.label = stem(f); c.params = {...(c.params || {}), [d.key]: f};
    su.cfg.models[nid] = c; made.push(nid);
  }
  su.varDlg = null; markDirty("config");
  toast(`${made.length} model${made.length === 1 ? "" : "s"} created – save to keep`);
}

// ------------------------------------------------------------------ editor (:1216-1247)
function ModelEdit({id, m}){
  const su = state.setup;
  const modes = modesOf(m);
  if(!modes.includes(su.mode)) su.mode = modes[0] || "txt2img";
  const sp = specOf(m, su.mode);
  const comfyModels = Object.keys((state.comfy.data && state.comfy.data.ok && state.comfy.data.models[id]) || {}).length;
  return html`<h2>${m.label || id} <span class="hint">${id}</span></h2>
    <div class="su-grid">
      <label>Display name</label><${Field} root="cfg" path=${["models", id, "label"]} />
      <label>Notes</label><${Field} root="cfg" path=${["models", id, "notes"]} />
      <label>Enabled</label><label style="text-align:left"><input type="checkbox" checked=${m.enabled !== false}
        onChange=${e => toggleEnabled(m, e)} /> included in <code>bench run</code> without <code>--models</code></label>
    </div>
    <h3>Workflows</h3>
    <table class="kv">${modes.length ? modes.map(mode => {
      const nmap = Object.keys(specOf(m, mode).map).length;
      return html`<tr key=${mode}><td class="k">${mode}</td><td><div class="row">
          <select value=${specOf(m, mode).file} style="flex:1" onChange=${e => wfChange(mode, e)}>
            <option value="">– select –</option>
            ${su.data.files.workflows.map(f => html`<option value=${f} selected=${f === specOf(m, mode).file}>${f}</option>`)}
            <option value="__upload">Upload …</option>
          </select>
          <button class=${su.mode === mode ? "btn-p" : ""} onClick=${() => { su.mode = mode; update(); }}>
            Mapping${nmap ? ` (${nmap})` : ""}</button></div></td>
        <td style="width:30px"><button class="x" title="Remove mode" onClick=${() => delMode(mode)}>✕</button></td></tr>`;
    }) : html`<tr><td class="hint">No workflow yet.</td></tr>`}</table>
    <div class="row" style="margin-top:8px"><button onClick=${addMode}>＋ Workflow for mode …</button>
      <span class="hint">txt2img is the default. Add your own workflow for img2img and similar.</span></div>
    ${sp ? html`<${Mapping} id=${id} m=${m} mode=${su.mode} sp=${sp} />` : ""}
    <h3>Parameter</h3>
    <p class="hint" style="margin-top:0">Values for mapped fields or <code>{{placeholder}}</code> that apply to this model. They override the defaults from "General", which prompts can in turn override.</p>
    <${ComfyBanner} />
    <${KvTable} root="cfg" path=${["models", id, "params"]} obj=${m.params || {}} optsOf=${k => comfyOpts(id, k)} />
    <h3>LoRAs</h3>
    <p class="hint" style="margin-top:0">Injected into the workflow during a run (Generate tab: one combination or a sweep).</p>
    <${Loras} id=${id} m=${m} />
    ${su.varDlg && su.varDlg.model === id ? html`<${VarFilesPanel} id=${id} m=${m} />` : ""}
    <div class="row" style="margin-top:14px;justify-content:space-between">
      <button disabled=${!comfyModels} title=${comfyModels ? undefined : "Needs a mapped choice field (e.g. checkpoint) and a reachable ComfyUI"}
        onClick=${() => {
          const keys = Object.keys(state.comfy.data.models[id]);
          const pref = ["checkpoint", "ckpt_name", "unet", "unet_name", "model"].find(k => keys.includes(k));
          su.varDlg = {model: id, key: pref || keys[0], sel: [], q: ""}; update();
        }}>Variants from files …</button>
      <button class="x" style="font-size:13px" onClick=${delModel}>Delete model</button></div>`;
}
