import {html, useEffect} from "cb/vendor/htm-preact.js";
import {state, update, reloadData, loadComfyOptions} from "cb/store.js";
import {api} from "cb/api.js";
import {toast} from "cb/components/toast.js";
import {uiConfirm} from "cb/components/dialog.js";
import {General, VarSets} from "cb/views/setup/general.js";
import {Models} from "cb/views/setup/models.js";
import {Prompts} from "cb/views/setup/prompts.js";
import {suErr} from "cb/views/setup/logic.js";

// ------------------------------------------------------------------ load / save / check (:1011-1070)
export async function suLoad(){
  const su = state.setup;
  su.loadError = "";
  try { su.data = await api("GET", "api/setup"); }
  catch(e){ su.loadError = e.message; update(); return; }
  su.cfg = su.data.config || {}; su.cfg.models = su.cfg.models || {};
  su.doc = su.data.prompts || {}; su.doc.prompts = su.doc.prompts || []; su.doc.vars = su.doc.vars || {};
  su.dirty = {config: false, prompts: false}; su.error = ""; su.analysis = {};
  su.sec = state.ui.suSec || su.sec || "models";
  if(!su.model || !su.cfg.models[su.model]) su.model = Object.keys(su.cfg.models)[0] || null;
  if(!su.prompt || !su.doc.prompts.some(p => p.id === su.prompt)) su.prompt = (su.doc.prompts[0] || {}).id || null;
  update();
  // after a save the mapping may have changed – fetch fresh lists (object_info stays cached on the server)
  state.comfy.data = null;
  loadComfyOptions();
}

export async function suSave(){
  const su = state.setup;
  su.error = "";
  try {
    if(su.dirty.config){ await api("PUT", "api/setup/config", su.cfg); su.dirty.config = false; }
    if(su.dirty.prompts){ await api("PUT", "api/setup/prompts", su.doc); su.dirty.prompts = false; }
    toast("Saved");
    await suLoad();
    await suRunCheck(true);
    reloadData().catch(() => {});
  } catch(e){ suErr(e.message); }
}

export async function suRunCheck(quiet){
  const su = state.setup;
  if(su.dirty.config || su.dirty.prompts){ return suSave(); }
  try {
    su.check = (await api("POST", "api/check", {})).results;
    update();
    if(!quiet) toast(su.check.every(r => r.ok) ? "All good" : "There are some notices");
  } catch(e){ suErr(e.message); }
}

// ------------------------------------------------------------------ frame (:1028-1053)
async function onDiscard(){
  if(await uiConfirm("All unsaved changes to config and prompts are lost.",
      {title: "Discard changes?", ok: "Discard", danger: true})) await suLoad();
}

function suBar(){
  const su = state.setup;
  const d = su.dirty.config || su.dirty.prompts;
  const label = d ? "Unsaved changes" + (su.dirty.config && su.dirty.prompts ? "" : su.dirty.config ? " (Config)" : " (Prompts)") : "All saved";
  return html`
    <div class="${d ? "dirty" : "hint"}">${label}</div>
    <button class="btn-p" disabled=${!d} onClick=${suSave}>Save</button>
    <div class="row"><button style="flex:1" onClick=${() => suRunCheck(false)}>Check</button><button style="flex:1" disabled=${!d} onClick=${onDiscard}>Discard</button></div>
    ${su.error ? html`<div class="su-msg err">${su.error}</div>` : ""}
    ${su.info ? html`<div class="hint">${su.info}</div>` : ""}`;
}
function suCheck(){
  const su = state.setup;
  if(!su.check) return null;
  return html`<b style="font-size:13px">Checks</b>${su.check.map(r =>
    html`<div>${r.ok ? "✓" : r.warn ? "⚠" : "✗"} <b>${r.model}</b> · ${r.mode}<br/><span class="hint">${r.msg}</span></div>`)}`;
}

const SECS = [["models", "Models"], ["prompts", "Prompts"], ["sets", "Variable Sets"], ["general", "General"]];

export function Setup(){
  const su = state.setup;
  useEffect(() => { if(!su.data) suLoad(); }, []);
  let body;
  if(su.loadError) body = html`<p class="empty">Setup not available: ${su.loadError}</p>`;
  else if(!su.data) body = html`<p class="empty">Loading setup …</p>`;
  else {
    let main;
    try {
      main = su.sec === "models" ? html`<${Models}/>`
        : su.sec === "prompts" ? html`<${Prompts}/>`
        : su.sec === "sets" ? html`<${VarSets}/>` : html`<${General}/>`;
    } catch(e){ main = html`<div class="su-msg err">${e.stack || e.message}</div>`; }
    body = html`<section class="su-main">${main}</section>`;
  }
  return html`<div id="setup"><div class="su">
    <aside class="su-nav">
      ${su.data ? SECS.map(([k, l]) => html`<button class="${su.sec === k ? "on" : ""}" onClick=${() => {
        su.sec = k; state.ui.suSec = k; su.error = ""; update();
      }}>${l}</button>`) : null}
      ${su.data ? html`<div class="su-bar">${suBar()}</div><div class="su-check">${suCheck()}</div>` : null}
    </aside>
    ${body}
  </div></div>`;
}
