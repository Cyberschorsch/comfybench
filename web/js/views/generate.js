import {html, useEffect} from "cb/vendor/htm-preact.js";
import {state, update, reloadData, loadComfyOptions, comfyOpts} from "cb/store.js";
import {api} from "cb/api.js";
import {uiConfirm} from "cb/components/dialog.js";
import {toast} from "cb/components/toast.js";
import {IMAGE_KEY, fmtDur} from "cb/util.js";
import {rnPrompts, rnLoraUnion, rnActive, rnModelsSec, rnPromptsSec, rnSweepsSec, rnLoraSec, rnImageInputsSec, rnStatusSec} from "cb/views/generate-parts.js";

// state.run.imgKeys[mid]: image-input keys of the first selected model's img2img
// workflow, fetched once per model id via api/workflow/analyze and cached here.
if(!state.run.imgKeys || typeof state.run.imgKeys !== "object") state.run.imgKeys = {};

let planTimer = null;
let pollTimer = null;
let pollWasActive = false;

// ------------------------------------------------------------------ selection / plan body

export function rnInitSelection(){
  const ui = state.ui;
  if(!Array.isArray(ui.rnModels)) ui.rnModels = (state.data.models || []).filter(m => m.enabled !== false).map(m => m.id);
  if(!Array.isArray(ui.rnPrompts)) ui.rnPrompts = rnPrompts().map(p => p.id);
  if(!Array.isArray(ui.rnSweeps)) ui.rnSweeps = [];
  if(!["base", "combo", "sweep"].includes(ui.rnLoraMode)) ui.rnLoraMode = "base";
  if(!Array.isArray(ui.rnLoraSel)) ui.rnLoraSel = [];
  if(!ui.rnLoraStr || typeof ui.rnLoraStr !== "object") ui.rnLoraStr = {};
  if(!["singles", "pairs", "all"].includes(ui.rnLoraSweep)) ui.rnLoraSweep = "all";
  // Remove orphaned entries (deleted models/prompts)
  ui.rnModels = ui.rnModels.filter(id => (state.data.models || []).some(m => m.id === id));
  ui.rnPrompts = ui.rnPrompts.filter(id => (state.data.prompts || []).some(p => p.id === id));
}

export function rnBody(){
  const sweep = {}, byModel = {};
  state.ui.rnSweeps.forEach(sw => { if(sw.name && sw.vals) sweep[sw.name.trim()] = sw.vals; });
  // Per model only the values its ComfyUI node accepts (empty → model is skipped for this sweep).
  Object.entries(sweep).forEach(([name, vals]) => state.ui.rnModels.forEach(mid => {
    const o = comfyOpts(mid, name); if(!o) return;
    (byModel[mid] = byModel[mid] || {})[name] = vals.split(",").map(v => v.trim()).filter(Boolean).filter(v => o.includes(v)).join(",");
  }));
  const body = {models: state.ui.rnModels, prompts: state.ui.rnPrompts, sweep,
    force: !!state.ui.rnForce, dry_run: !!state.ui.rnDry, images: state.ui.rnImages || {}};
  if(Object.keys(byModel).length) body.sweep_by_model = byModel;
  if(state.ui.rnLoraMode === "combo"){
    const names = rnLoraUnion().map(x => x.name).filter(n => state.ui.rnLoraSel.includes(n));
    if(names.length){
      const parts = names.map(n => {
        const st = String(state.ui.rnLoraStr[n] ?? "").trim();
        if(st && !Number.isFinite(Number(st))) throw new Error(`LoRA strength for "${n}" must be a number (e.g. 0.8)`);
        return st ? `${n}:${st}` : n;
      });
      body.lora_combo = [parts.join(",")];
    }
  }
  else if(state.ui.rnLoraMode === "sweep") body.sweep_loras = state.ui.rnLoraSweep || "all";
  return body;
}

// ------------------------------------------------------------------ polling

export async function rnPoll(){
  clearTimeout(pollTimer);
  let st;
  try { st = await api("GET", "api/run/status"); } catch(e){ return; }
  state.run.status = st;
  const active = rnActive();
  if(pollWasActive && !active) state.run.planRev = (state.run.planRev || 0) + 1;
  pollWasActive = active;
  update();
  const doneNow = st.done || 0;
  if(doneNow !== state.run.lastDone && !st.dry_run && (Date.now() - state.run.lastReload > 5000 || !active)){
    state.run.lastDone = doneNow; state.run.lastReload = Date.now();
    await reloadData();
  }
  if(active) pollTimer = setTimeout(rnPoll, 1500);
}

export function startPolling(){ if(!state.isStatic) rnPoll(); }

export function navRunLabel(){
  const st = state.run.status;
  if(!st || !rnActive()) return "Generate";
  const f = (st.done || 0) + (st.skipped || 0) + (st.failed || 0) + (st.idle || 0);
  return `Generate · ${f}/${st.total || 0}`;
}

async function onStart(){
  let body;
  try { body = rnBody(); } catch(e){ state.run.planErr = e.message; update(); return; }
  try {
    state.run.status = await api("POST", "api/run/start", body);
    state.run.lastDone = -1;
    update();
    rnPoll();
  } catch(e){ state.run.planErr = e.message; update(); }
}

async function onStop(hard){
  try {
    state.run.status = await api("POST", "api/run/stop", {hard});
    update();
    rnPoll();
  } catch(e){ toast(e.message); }
}

async function onKill(){
  if(!await uiConfirm("The current image in ComfyUI is aborted and the run ends.", {title: "Abort run?", ok: "Abort run", danger: true})) return;
  onStop(true);
}

// ------------------------------------------------------------------ Plan / start

function rnPlanSec(){
  const ui = state.ui;
  if(!ui.rnModels.length || !ui.rnPrompts.length) return html`<div class="rn-plan" id="rn-plan"><span class="hint">Select at least one model and one prompt.</span></div>`;
  if(state.run.planErr) return html`<div class="rn-plan" id="rn-plan"><div class="warnline">${state.run.planErr}</div></div>`;
  const pl = state.run.plan;
  if(!pl) return html`<div class="rn-plan" id="rn-plan"><span class="hint">Calculating …</span></div>`;
  const xs = (state.data.generations || []).filter(g => g.status === "ok" && g.seconds && ui.rnModels.includes(g.model)).map(g => g.seconds);
  const avg = xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  return html`<div class="rn-plan" id="rn-plan"><b>${pl.new} new image${pl.new === 1 ? "" : "s"}</b> · ${pl.total} jobs total${pl.existing ? `, ${pl.existing} already exist` : ""}${pl.idle ? `, ${pl.idle} skipped because of sweep` : ""}
    ${avg && pl.new && !ui.rnDry ? html`<div class="hint">estimated about ${fmtDur(avg * pl.new)} (Ø ${avg.toFixed(1)} s per image so far)</div>` : ""}
    ${pl.warnings.map(w => html`<div class="hint">⚠ ${w}</div>`)}
    ${pl.errors.length ? html`<div class="warnline">${pl.failed} job${pl.failed === 1 ? "" : "s"} cannot be filled:${pl.errors.slice(0, 5).map((w, i) => [i ? html`<br/>` : "", w])}</div>` : ""}
    ${pl.total && !pl.new && !pl.failed ? html`<div class="hint">Everything already generated. Tick "Regenerate existing images anyway" for new versions.</div>` : ""}</div>`;
}

function rnStartBtn(){
  const ui = state.ui;
  const can = !!(state.run.plan && state.run.plan.new > 0);
  return html`<button class="btn-p rn-start" id="rn-start" disabled=${!can || rnActive()} onClick=${onStart}>${rnActive() ? "Running …" : ui.rnDry ? "Start dry run" : "Start generating"}</button>`;
}

// ------------------------------------------------------------------ Generate view

export function Generate(){
  rnInitSelection();
  const ui = state.ui;

  let body = null, bodyErr = "";
  try { body = rnBody(); } catch(e){ bodyErr = e.message; }
  if(bodyErr) state.run.planErr = bodyErr;
  const bodyKey = JSON.stringify(body || null);

  // ComfyUI options: load once, then re-render (via update() inside loadComfyOptions)
  useEffect(() => {
    if(!state.comfy.data && !state.comfy.loading) loadComfyOptions();
  }, []);

  // Plan: debounced 250 ms, keyed by the plan body; rnBody errors → planErr
  useEffect(() => {
    if(bodyErr || !ui.rnModels.length || !ui.rnPrompts.length){
      state.run.plan = null;
      if(!bodyErr) state.run.planErr = "";
      return;
    }
    planTimer = setTimeout(async () => {
      try { state.run.plan = await api("POST", "api/run/plan", body); state.run.planErr = ""; }
      catch(e){ state.run.plan = null; state.run.planErr = e.message; }
      update();
    }, 250);
    return () => clearTimeout(planTimer);
  }, [bodyKey, bodyErr, String(state.run.planRev || 0)]);

  // Image-input workflow analysis: once per selected model id, cached in run.imgKeys
  useEffect(() => {
    const mid = ui.rnModels[0];
    if(!mid || state.run.imgKeys[mid] !== undefined) return;
    let cancelled = false;
    (async () => {
      const m = state.idx.modelMap.get(mid);
      let wfFile = null;
      if(m && (m.modes || []).includes("img2img") && m.workflows && m.workflows.img2img){
        const w = m.workflows.img2img;
        wfFile = typeof w === "string" ? w : w.file;
      }
      if(!wfFile){ if(!cancelled) state.run.imgKeys[mid] = []; return; }
      let keys = [];
      try {
        const analysis = await api("POST", "api/workflow/analyze", {file: wfFile});
        keys = Object.keys(analysis.suggest || {}).filter(k => IMAGE_KEY.test(k));
      } catch(e){}
      if(!cancelled){ state.run.imgKeys[mid] = keys; update(); }
    })();
    return () => { cancelled = true; };
  }, [ui.rnModels[0]]);

  return html`<div id="run">
    <div class="rn">
      <div class="rn-card">
        <h2>What should be generated?</h2>
        ${rnModelsSec()}
        ${rnPromptsSec()}
        ${rnImageInputsSec()}
        ${rnSweepsSec()}
        ${rnLoraSec()}
        <div class="rn-sec"><label class="rn-item"><input type="checkbox" checked=${ui.rnForce} onChange=${e => { ui.rnForce = e.currentTarget.checked; update(); }} /><span>Regenerate existing images anyway<small>creates additional versions</small></span></label>
          <label class="rn-item"><input type="checkbox" checked=${ui.rnDry} onChange=${e => { ui.rnDry = e.currentTarget.checked; update(); }} /><span>Dry run without ComfyUI<small>only fills the workflows (results/_dryrun)</small></span></label></div>
        ${rnPlanSec()}
        ${rnStartBtn()}
      </div>
      ${rnStatusSec(onStop, onKill)}
    </div>
  </div>`;
}
