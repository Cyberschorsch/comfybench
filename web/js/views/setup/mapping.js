import {html, useEffect} from "cb/vendor/htm-preact.js";
import {api} from "cb/api.js";
import {state, update} from "cb/store.js";
import {uiConfirm, uiPrompt} from "cb/components/dialog.js";
import {asList, nameRule, RE_NAME} from "cb/util.js";
import {specOf, setSpec, hasValue, curModel, markDirty, applySuggestions, fillFromWorkflow} from "cb/views/setup/logic.js";

// ------------------------------------------------------------------ analysis (:1341-1348)
const pending = {};
export async function ensureAnalysis(){
  const su = state.setup, m = curModel(); if(!m) return null;
  const sp = specOf(m, su.mode); if(!sp || !sp.file) return null;
  const file = sp.file;
  if(su.analysis[file]) return su.analysis[file];
  if(!pending[file]){
    pending[file] = api("POST", "api/workflow/analyze", {file})
      .catch(e => ({error: e.message}))
      .then(an => { state.setup.analysis[file] = an; delete pending[file]; update(); return an; });
  }
  return pending[file];
}

// ------------------------------------------------------------------ mapping panel (:1291-1314)
export function Mapping({id, m, mode, sp}){
  const su = state.setup;
  const file = sp ? sp.file : "";
  useEffect(() => { if(file) ensureAnalysis(); }, [su.model, mode, file]);
  const an = file ? su.analysis[file] : null;
  const keys = Object.keys(sp.map);
  const missing = keys.filter(k => !hasValue(m, k));
  const sum = keys.map(k => {
    const ok = hasValue(m, k);
    return html`<span class=${ok ? "" : "warn"} title=${ok ? "" : "No value: set as a parameter"}>
      <b>${k}</b> → ${asList(sp.map[k]).join(", ")}${ok ? "" : " ⚠"}</span>`;
  });
  let body;
  if(!file) body = html`<p class="hint">Select a workflow file first.</p>`;
  else if(!an) body = html`<p class="hint">Analyzing workflow …</p>`;
  else if(an.error) body = html`<div class="su-msg err">${an.error}</div>`;
  else body = html`<${Nodes} m=${m} sp=${sp} an=${an} />`;
  return html`<h3>Mapping · ${mode} <span class="hint">${file}</span></h3>
    <p class="hint" style="margin-top:0">Which field in the workflow gets which value? One value can go into several fields (e.g. seed in Sampler and LLM). A mapped field is overwritten during the run, even if there is a link.</p>
    <div class="mapsum">${sum.length ? sum : html`<span>nothing mapped yet</span>`}</div>
    ${missing.length ? html`<div class="warnline">No value: ${missing.join(", ")}.
      <button onClick=${() => fillMissing(m, mode)}>Take values from workflow</button></div>` : ""}
    ${an && an.placeholders && an.placeholders.length ? html`<p class="hint">The workflow already contains placeholders:
      ${an.placeholders.map(p => html`<code>{{${p}}}</code>`)} – these keep working.</p>` : ""}
    ${an && !an.error && !an.has_output ? html`<div class="warnline">No Save/Preview Image node found – the workflow will not deliver an image this way.</div>` : ""}
    <div class="row" style="margin:8px 0">
      <button disabled=${!(an && !an.error)} onClick=${() => applySugg(m, mode)}>Apply suggestions</button>
      <button onClick=${() => clearMap(m, mode)}>Clear mapping</button>
      <input type="search" placeholder="Search node or field…" value=${su.nodeQ} style="width:200px"
        onInput=${e => { su.nodeQ = e.currentTarget.value; update(); }} />
      <label><input type="checkbox" checked=${su.allNodes} onChange=${e => { su.allNodes = e.currentTarget.checked; update(); }} /> include pure link nodes</label>
    </div>${body}`;
}

function applySugg(m, mode){
  const su = state.setup;
  applySuggestions(m, mode, false);
  markDirty("config"); su.info = "Suggestions applied."; update();
}
async function clearMap(m, mode){
  if(!await uiConfirm(`All input mappings for ${mode} are removed.`,
      {title: "Clear mapping?", ok: "Clear", danger: true})) return;
  const sp = specOf(m, mode); sp.map = {}; setSpec(m, mode, sp);
  markDirty("config");
}
function fillMissing(m, mode){
  const sp = specOf(m, mode);
  fillFromWorkflow(m, mode, Object.keys(sp.map).filter(k => !hasValue(m, k)));
  markDirty("config");
}

// ------------------------------------------------------------------ nodes (:1315-1340)
export function Nodes({m, sp, an}){
  const su = state.setup;
  const rev = {};
  Object.entries(sp.map).forEach(([k, t]) => asList(t).forEach(x => rev[x] = k));
  const sugg = {};
  Object.entries(an.suggest || {}).forEach(([k, t]) => t.forEach(x => sugg[x] = k));
  const custom = [...new Set([...Object.keys(sp.map), ...Object.keys(m.params || {})])].filter(k => !su.data.standard_keys.includes(k));
  const keys = [...su.data.standard_keys, ...custom];
  const q = su.nodeQ.trim().toLowerCase();
  const rows = an.nodes.map(n => {
    const hay = (n.id + " " + n.class_type + " " + n.title + " " + n.inputs.map(i => i.name).join(" ")).toLowerCase();
    if(q && !hay.includes(q)) return "";
    const anyMapped = n.inputs.some(i => rev[`${n.id}/${i.name}`]);
    if(!su.allNodes && !anyMapped && !n.inputs.some(i => !i.link)) return "";
    return html`<div class="node" key=${n.id}><div class="nh">${n.title || n.class_type}
        <small>#${n.id} · ${n.class_type}</small></div>
      ${n.inputs.map(i => {
        const t = `${n.id}/${i.name}`, cur = rev[t] || "";
        const v = i.link ? `→ Node ${i.value[0]}` : (typeof i.value === "string" ? JSON.stringify(i.value) : String(i.value));
        return html`<div class=${"inp" + (cur ? " mapped" : "")}>
          <span class="n">${i.name}</span>
          <span class="v" title=${v.slice(0, 600)}>${v.slice(0, 140)}${!cur && sugg[t] ? html` <span class="hint" title="Suggestion">· Suggestion: ${sugg[t]}</span>` : ""}</span>
          <select value=${cur} onChange=${e => mapChange(t, cur, e)}>
            <option value="">—</option>
            ${keys.map(k => html`<option value=${k} selected=${k === cur}>${k}</option>`)}
            <option value="__new">＋ custom name …</option>
          </select></div>`;
      })}</div>`;
  });
  return html`<div class="nodes">${rows.some(r => r !== "") ? rows : html`<p class="hint" style="padding:8px">No matching nodes.</p>`}</div>`;
}

async function mapChange(t, cur, e){
  const el = e.currentTarget, sp = specOf(curModel(), state.setup.mode);
  let key = el.value;
  if(key === "__new"){
    key = await uiPrompt("New value name", {placeholder: "e.g. steps", ok: "Add",
      validate: nameRule(RE_NAME, "Letters, digits and _")});
    if(!key){ el.value = cur; return; }
  }
  for(const k of Object.keys(sp.map)){
    const l = asList(sp.map[k]).filter(x => x !== t);
    if(l.length) sp.map[k] = l; else delete sp.map[k];
  }
  if(key) sp.map[key] = [...asList(sp.map[key]), t];
  setSpec(curModel(), state.setup.mode, sp);
  markDirty("config");
}
