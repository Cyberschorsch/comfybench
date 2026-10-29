import {html} from "cb/vendor/htm-preact.js";
import {state, update, reloadData, comfyOpts, mLabel} from "cb/store.js";
import {api, fileUrl, pickFile, readDataUrl} from "cb/api.js";
import {toast} from "cb/components/toast.js";
import {openLightbox} from "cb/views/lightbox.js";
import {splitVals, IMAGE_KEY, fmtDur} from "cb/util.js";

// ------------------------------------------------------------------ selection helpers

export function rnPrompts(){ return (state.data.prompts || []).filter(p => p.id !== "_import"); }
export function rnBaseVariants(p){ return p.variants.filter(v => !v.extra).length || 1; }
export function rnLoraModels(){ return state.ui.rnModels.map(mid => state.idx.modelMap.get(mid)).filter(Boolean); }
export function rnActive(){ const st = state.run.status; return !!(st && ["running", "stopping"].includes(st.state)); }

// LoRAs of the currently selected models: union in config order, with the
// models that do not define a LoRA recorded for the "skipped for …" hint.
export function rnLoraUnion(){
  const sel = rnLoraModels(), out = [];
  sel.forEach(m => Object.entries(m.loras || {}).forEach(([name, info]) => {
    let e = out.find(x => x.name === name);
    if(!e){ e = {name, info: info || {}}; out.push(e); }
  }));
  out.forEach(e => e.missing = sel.filter(m => !(m.loras || {})[e.name]).map(m => m.label));
  return out;
}
export function rnLoraSweepCount(m, mode){
  const n = Object.keys(m.loras || {}).length;
  if(!n) return 1;
  if(mode === "singles") return n;
  if(mode === "pairs") return n * (n - 1) / 2;
  return Math.pow(2, n) - 1;
}

// Sweeps over choice fields: union of the ComfyUI options of the selected models
export function rnSweepOptions(name){
  const out = []; name = (name || "").trim(); if(!name) return out;
  rnLoraModels().forEach(m => (comfyOpts(m.id, name) || []).forEach(o => {
    o = String(o);   // ComfyUI choice inputs may be numeric; sweep values are strings
    let e = out.find(x => x.o === o); if(!e) out.push(e = {o, models: []}); e.models.push(m.label);
  }));
  return out;
}

// ------------------------------------------------------------------ Models / prompts

export function rnModelsSec(){
  const ui = state.ui, d = state.data || {models: []};
  const selM = new Set(ui.rnModels);
  return html`<div class="rn-sec"><div class="rn-h">Models <span><button onClick=${() => { ui.rnModels = (d.models || []).map(m => m.id); update(); }}>all</button><button onClick=${() => { ui.rnModels = []; update(); }}>none</button></span></div>
    ${(d.models || []).length ? (d.models || []).map(m => html`<label class="rn-item"><input type="checkbox" data-rn-model=${m.id} checked=${selM.has(m.id)} onChange=${e => {
      const id = e.currentTarget.dataset.rnModel;
      ui.rnModels = e.currentTarget.checked ? [...new Set([...ui.rnModels, id])] : ui.rnModels.filter(x => x !== id);
      update();
    }} /><span>${m.label}<small>${m.modes.join(", ") || "no workflow"}${m.enabled === false ? " · inactive" : ""}</small></span></label>`) : html`<p class="hint">No models yet – create them under Setup.</p>`}
  </div>`;
}

export function rnPromptsSec(){
  const ui = state.ui;
  const selP = new Set(ui.rnPrompts);
  const cats = {};
  rnPrompts().forEach(p => (cats[p.category || "uncategorized"] = cats[p.category || "uncategorized"] || []).push(p));
  return html`<div class="rn-sec"><div class="rn-h">Prompts <span><button onClick=${() => { ui.rnPrompts = rnPrompts().map(p => p.id); update(); }}>all</button><button onClick=${() => { ui.rnPrompts = []; update(); }}>none</button></span></div>
    <div class="rn-plist">${Object.entries(cats).map(([c, ps]) => {
      const n = ps.filter(p => selP.has(p.id)).length;
      return html`<div class="rn-cat"><label class="rn-item rn-cathead"><input type="checkbox" data-rn-cat=${c} checked=${n === ps.length} ref=${el => { if(el) el.indeterminate = n > 0 && n < ps.length; }} onChange=${e => {
        const ids = ps.map(p => p.id);
        ui.rnPrompts = e.currentTarget.checked ? [...new Set([...ui.rnPrompts, ...ids])] : ui.rnPrompts.filter(x => !ids.includes(x));
        update();
      }} /><b>${c}</b> <small>${n}/${ps.length}</small></label>
        ${ps.map(p => { const v = rnBaseVariants(p);
          return html`<label class="rn-item rn-p"><input type="checkbox" data-rn-prompt=${p.id} checked=${selP.has(p.id)} onChange=${e => {
            const id = e.currentTarget.dataset.rnPrompt;
            ui.rnPrompts = e.currentTarget.checked ? [...new Set([...ui.rnPrompts, id])] : ui.rnPrompts.filter(x => x !== id);
            update();
          }} /><span>${p.id}${v > 1 ? html` <small>×${v} variants</small>` : ""}${p.mode !== "txt2img" ? html` <span class="tag">${p.mode}</span>` : ""}</span></label>`; })}
      </div>`;
    })}</div></div>`;
}

// ------------------------------------------------------------------ Sweeps

function onSwVal(sw, f, value){ sw[f] = value; update(); }
function onSwOpt(i, o, checked){
  const sw = state.ui.rnSweeps[i];
  const vals = splitVals(sw.vals).filter(x => x !== o);
  if(checked) vals.push(o);
  sw.vals = vals.join(", ");
  update();
}
function onSwAllNone(i, act){
  const sw = state.ui.rnSweeps[i]; if(!sw) return;
  const q = (state.run.swq[i] || "").trim().toLowerCase();
  const shown = rnSweepOptions(sw.name).map(e => e.o).filter(o => !o.includes(",") && (!q || o.toLowerCase().includes(q)));
  const cur = splitVals(sw.vals);
  sw.vals = (act === "sw-all" ? [...new Set([...cur, ...shown])] : cur.filter(x => !shown.includes(x))).join(", ");
  update();
}
function onAddSweep(){ state.ui.rnSweeps.push({name: "", vals: ""}); update(); }
function onDelSweep(i){ state.ui.rnSweeps.splice(i, 1); state.run.swq = {}; update(); }

function rnSwPicker(sw, i){
  const sel = new Set(splitVals(sw.vals));
  const q = (state.run.swq[i] || "").trim().toLowerCase();
  const opts = rnSweepOptions(sw.name);
  const nModels = state.ui.rnModels.filter(mid => comfyOpts(mid, sw.name.trim())).length;
  const items = opts.filter(e => !q || e.o.toLowerCase().includes(q)).map(e => {
    const comma = e.o.includes(",");
    return html`<label class="rn-item" title=${comma ? "Contains a comma – cannot be swept" : undefined}><input type="checkbox" data-rn-swopt=${i} data-o=${e.o} checked=${sel.has(e.o)} disabled=${comma} onChange=${e => onSwOpt(i, e.currentTarget.dataset.o, e.currentTarget.checked)} /><span>${e.o}${e.models.length < nModels ? html` <small>${e.models.join(", ")}</small>` : ""}</span></label>`;
  });
  return html`<div style="flex:1;min-width:220px"><div class="row"><input type="search" placeholder="Search …" data-rn-swq=${i} value=${state.run.swq[i] || ""} style="flex:1" onInput=${e => { state.run.swq[i] = e.currentTarget.value; update(); }} />
    <button onClick=${() => onSwAllNone(i, "sw-all")}>all</button>
    <button onClick=${() => onSwAllNone(i, "sw-none")}>none</button>
    <span class="hint">${sel.size} selected</span></div>
    <div class="rn-sw-list">${items.length ? items : html`<span class="hint">No matching options.</span>`}</div></div>`;
}

export function rnSweepsSec(){
  const ui = state.ui, d = state.data || {models: []};
  const selM = new Set(ui.rnModels);
  const keys = [...new Set(d.models.filter(m => selM.has(m.id)).flatMap(m => m.keys || []))].sort();
  const sweeps = ui.rnSweeps.map((sw, i) => {
    const lacking = sw.name ? d.models.filter(m => selM.has(m.id) && !(m.keys || []).includes(sw.name)).map(m => m.label) : [];
    const opts = rnSweepOptions(sw.name);
    const vals = opts.length ? rnSwPicker(sw, i)
      : html`<input type="text" placeholder="e.g. 3, 5, 7" value=${sw.vals || ""} style="flex:1;min-width:140px" onChange=${e => onSwVal(sw, "vals", e.currentTarget.value)} />`;
    return html`<div class="row rn-sweep"><input type="text" list="rn-keys" placeholder="Value, e.g. cfg" value=${sw.name || ""} style="width:150px" onChange=${e => onSwVal(sw, "name", e.currentTarget.value)} />
      ${vals}
      <button class="x" onClick=${() => onDelSweep(i)}>✕</button>
      ${lacking.length ? html`<div class="hint" style="width:100%">skipped for: ${lacking.join(", ")}</div>` : ""}</div>`;
  });
  return html`<div class="rn-sec"><div class="rn-h">Sweeps <span class="hint">extra axis for all selected prompts</span></div>
    ${sweeps}<datalist id="rn-keys">${keys.map(k => html`<option>${k}</option>`)}</datalist>
    <button onClick=${onAddSweep}>＋ Sweep</button></div>`;
}

// ------------------------------------------------------------------ LoRAs

export function rnLoraSec(){
  const ui = state.ui;
  const loraUnion = rnLoraUnion();
  if(!loraUnion.length) return null;
  const loraSel = new Set(ui.rnLoraSel);
  const radios = [["base", "Base (no LoRA)"], ["combo", "One combination"], ["sweep", "Sweep"]].map(([v, l]) =>
    html`<label class="rn-item"><input type="radio" name="rn-lora-mode" data-rn-lora-mode=${v} checked=${ui.rnLoraMode === v} onChange=${e => { ui.rnLoraMode = e.currentTarget.dataset.rnLoraMode; update(); }} /><span>${l}</span></label>`);
  let body = null;
  if(ui.rnLoraMode === "combo"){
    body = loraUnion.map(x => {
      const on = loraSel.has(x.name), dflt = x.info.default_strength;
      return html`<div class="row rn-lora"><label class="rn-item" style="flex:1;min-width:170px"><input type="checkbox" data-rn-lora=${x.name} checked=${on} onChange=${e => {
        const n = e.currentTarget.dataset.rnLora;
        ui.rnLoraSel = e.currentTarget.checked ? [...new Set([...ui.rnLoraSel, n])] : ui.rnLoraSel.filter(x => x !== n);
        update();
      }} /><span>${x.name}<small>${x.info.trigger ? `trigger: “${x.info.trigger}”` : "no trigger word"}${dflt != null ? ` · default ${dflt}` : ""}</small></span></label>
        <input type="text" inputmode="decimal" data-rn-lora-str=${x.name} value=${ui.rnLoraStr[x.name] || ""} placeholder=${dflt != null ? `strength (${dflt})` : "strength"} disabled=${!on} style="width:140px" onInput=${e => {
          const n = e.currentTarget.dataset.rnLoraStr;
          ui.rnLoraStr = ui.rnLoraStr || {}; ui.rnLoraStr[n] = e.currentTarget.value;
          update();
        }} />
        ${x.missing.length ? html`<div class="hint">skipped for: ${x.missing.join(", ")}</div>` : ""}</div>`;
    });
    const n = loraUnion.filter(x => loraSel.has(x.name)).length;
    if(!n) body = [...body, html`<div class="hint">Nothing checked – runs without LoRA.</div>`];
  }
  else if(ui.rnLoraMode === "sweep"){
    const opts = [["singles", "every LoRA alone"], ["pairs", "every pair"], ["all", "every combination"]].map(([v, l]) =>
      html`<option value=${v} selected=${ui.rnLoraSweep === v}>${l}</option>`);
    const counts = rnLoraModels().map(m => {
      const n = Object.keys(m.loras || {}).length;
      const c = rnLoraSweepCount(m, ui.rnLoraSweep);
      return n ? `${m.label}: ${c} combination${c === 1 ? "" : "s"}` : `${m.label}: base only (no LoRAs)`;
    }).join(" · ");
    body = html`<div class="row rn-lora"><label class="rn-item"><span>Combine</span></label>
      <select onChange=${e => { ui.rnLoraSweep = e.currentTarget.value; update(); }}>${opts}</select></div>
      <div class="hint">${counts}</div>`;
  }
  return html`<div class="rn-sec"><div class="rn-h">LoRAs <span class="hint">injected into the workflow · optional</span></div>
    <div class="rn-lora-modes">${radios}</div>${body ? html`<div>${body}</div>` : ""}</div>`;
}

// ------------------------------------------------------------------ Image inputs

async function onImgUpload(prompt, key){
  const f = await pickFile("image/png,image/jpeg,image/webp");
  if(!f) return;
  const dataUrl = await readDataUrl(f);
  try {
    const r = await api("POST", "api/upload", {kind: "input", name: f.name, data: dataUrl});
    const suData = state.setup.data;
    if(suData && suData.files && suData.files.inputs){
      suData.files.inputs = [...new Set([...suData.files.inputs, r.path])].sort();
    }
    state.ui.rnImages = state.ui.rnImages || {};
    state.ui.rnImages[prompt] = state.ui.rnImages[prompt] || {};
    state.ui.rnImages[prompt][key] = r.path;
    update();
  } catch(e){ toast(e.message); }
}

function onImgChange(prompt, key, value, sel){
  if(value === "__upload"){ if(sel) sel.value = (state.ui.rnImages?.[prompt]?.[key]) || ""; onImgUpload(prompt, key); return; }
  const imgs = state.ui.rnImages = state.ui.rnImages || {};
  imgs[prompt] = imgs[prompt] || {};
  if(value) imgs[prompt][key] = value; else delete imgs[prompt][key];
  if(Object.keys(imgs[prompt] || {}).length === 0) delete imgs[prompt];
  update();
}

function imgPreview(f){
  const imgs = state.ui.rnImages || {};
  const val = (imgs[f.prompt] && imgs[f.prompt][f.key]) || "";
  if(!val) return null;
  let url = "";
  if(val.startsWith("gen:")){
    const g = (state.data.generations || []).find(x => x.id === val.slice(4));
    if(g?.files?.[0]) url = fileUrl(g.files[0]);
  } else url = "inputs/" + val;
  if(url) return html`<div><img src=${url} alt="Preview" style="max-width:200px;max-height:200px;border-radius:6px;border:1px solid var(--line);background:var(--bg)" />
    <div class="hint" style="margin-top:4px;font-size:11px">${val}</div></div>`;
  return html`<span class="hint">No preview available</span>`;
}

async function onThumb(id){
  if(!state.idx.byId.has(id)) await reloadData();
  if(state.idx.byId.has(id)) openLightbox(id, ((state.run.status && state.run.status.recent) || []).map(r => r.id).filter(x => state.idx.byId.has(x)));
}

export function rnImageInputsSec(){
  const ui = state.ui;
  const selected = ui.rnPrompts.filter(id => state.idx.promptMap.has(id));
  const mid = ui.rnModels[0];
  const mappedKeys = (state.run.imgKeys && state.run.imgKeys[mid]) || [];
  const fields = [];
  for(const pid of selected){
    const p = state.idx.promptMap.get(pid);
    if(!p) continue;
    for(const [k, v] of Object.entries(p)){
      if(IMAGE_KEY.test(k) && typeof v === "string" && mappedKeys.includes(k)){
        fields.push({prompt: pid, key: k, value: v, label: k === "image" ? "Main image" : `Image ${k.slice(5) || "2"}`});
      }
    }
  }
  if(!fields.length) return null;
  const genOpts = (state.data.generations || []).filter(g => g.status === "ok" && g.files && g.files.length)
    .sort((a, b) => b._i - a._i).slice(0, 50);
  const inputs = (state.setup.data && state.setup.data.files && state.setup.data.files.inputs) || [];
  return fields.map(f => {
    const imgs = state.ui.rnImages || {};
    const val = (imgs[f.prompt] && imgs[f.prompt][f.key]) || "";
    return html`<div class="rn-sec" style="border-left:3px solid var(--accent);padding-left:12px;margin-left:-3px">
      <div class="rn-h">${f.label} for <b>${f.prompt}</b></div>
      <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
        <select data-rn-img=${f.prompt} data-rn-img-key=${f.key} style="flex:1;min-width:200px" value=${val} onChange=${e => onImgChange(f.prompt, f.key, e.currentTarget.value, e.currentTarget)}>
          <option value="">– select –</option><option value="__upload">📤 Upload …</option>
          ${inputs.length ? html`<optgroup label="From inputs">${inputs.map(x => html`<option>${x}</option>`)}</optgroup>` : ""}
          ${genOpts.length ? html`<optgroup label="Previous generations">${genOpts.map(g => html`<option value="gen:${g.id}">${g.id.slice(0,8)} · ${mLabel(g.model)} · ${g.prompt.slice(0,30)}</option>`)}</optgroup>` : ""}
        </select>
        <span class="hint" style="flex:1 1 100%;font-size:11px">${f.value || "no default"}</span>
      </div>
      <div class="rn-img-preview" style="margin-top:8px;min-height:64px">${imgPreview(f)}</div>
    </div>`;
  });
}

// ------------------------------------------------------------------ Status

export function rnStatusSec(onStop, onKill){
  const st = state.run.status || {state: "idle"};
  const labels = {idle: "Ready", running: "Running", stopping: "Stopping …", done: "Done", stopped: "Stopped", error: "Error"};
  const finished = (st.done || 0) + (st.skipped || 0) + (st.failed || 0) + (st.idle || 0);
  const pct = st.total ? Math.round(100 * finished / st.total) : 0;
  const active = rnActive();
  const dry = st.dry_run ? " · Dry run" : "";
  return html`<div class="rn-card" id="rn-status">
    <h2>Status <span class="rn-badge rn-${st.state}">${labels[st.state] || st.state}${dry}</span></h2>
    ${st.state === "idle" ? html`<p class="hint">No run yet since the gallery started. Runs from the terminal (<code>bench run</code>) do not show up here, but their images do in the gallery.</p>` :
    html`<div class="rn-bar"><div style="width:${pct}%"></div></div>
    <div class="rn-nums"><span><b>${st.done || 0}</b> ${st.dry_run ? "filled" : "generated"}</span><span><b>${st.skipped || 0}</b> skipped</span><span class=${st.failed ? "rn-errn" : ""}><b>${st.failed || 0}</b> errors</span><span>${finished}/${st.total || 0}</span>
      ${active && st.eta != null ? html` <span>about ${fmtDur(st.eta)} left</span>` : ""}</div>
    ${st.current ? html`<div class="rn-cur"><span class="rn-spin"></span>${st.current}</div>` : ""}
    ${st.msg && st.state === "error" ? html`<div class="su-msg err">${st.msg}</div>` : ""}
    ${active ? html`<div class="row" style="margin:10px 0"><button onClick=${() => onStop(false)}>Stop after the current image</button><button class="x" style="font-size:13px" onClick=${() => onKill()}>Abort now</button></div>` : ""}
    ${(st.recent || []).length ? html`<div class="rn-h" style="margin-top:12px">Latest images</div>
      <div class="rn-thumbs">${(st.recent || []).map(r => html`<img src=${fileUrl(r.file)} data-rn-img=${r.id} title=${mLabel(r.model) + " · " + r.prompt + (Object.keys(r.variant || {}).length ? " · " + Object.entries(r.variant || {}).map(([k, v]) => k + ": " + v).join(", ") : "") + " · seed " + r.seed} alt="" onClick=${() => onThumb(r.id)} />`)}</div>` : ""}
    <div class="rn-h" style="margin-top:12px">Log <span class="hint">${(st.started || "").replace("T", " ")}${st.finished ? " – " + st.finished.slice(11) : ""}</span></div>
    <div class="rn-log">${(st.log || []).slice().reverse().map(l => html`<div class="l-${l.level}"><span>${l.t}</span> ${l.msg}</div>`)}</div>`}
  </div>`;
}
