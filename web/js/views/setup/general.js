import {html, useRef} from "cb/vendor/htm-preact.js";
import {state} from "cb/store.js";
import {renameKey} from "cb/util.js";
import {Field, KvTable} from "cb/views/setup/field.js";
import {markDirty, suErr, defnToText, textToDefn} from "cb/views/setup/logic.js";

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ------------------------------------------------------------------ General (:1510-1521)
export function General(){
  const su = state.setup;
  return html`<div class="su-edit"><h2>General</h2>
    <div class="su-grid">
      <label>ComfyUI address</label><div><${Field} root="cfg" path=${["server"]} placeholder="http://127.0.0.1:8188" /><div class="hint">In the Docker container, COMFYUI_URL takes precedence.</div></div>
      <label>Seeds</label><${Field} root="cfg" path=${["seeds"]} type="seeds" placeholder="42, 1337" />
      <label>Timeout per image (s)</label><${Field} root="cfg" path=${["timeout"]} type="scalar" class="short" />
      <label>Results folder</label><div><${Field} root="cfg" path=${["output_dir"]} placeholder="results" /><div class="hint">Changes take effect after restarting <code>bench serve</code>.</div></div>
    </div>
    <h3>Defaults</h3>
    <p class="hint" style="margin-top:0">Apply to all models unless the model or prompt says otherwise.</p>
    <${KvTable} root="cfg" path=${["defaults"]} obj=${su.cfg.defaults || {}} />
  </div>`;
}

// ------------------------------------------------------------------ Variable Sets (:1500-1508 + rename/delete)
// Set values textarea: same raw-text rule as Field, but the text maps through textToDefn.
function SetValues({name, defn}){
  const raw = useRef(null);
  const shown = raw.current !== null && same(textToDefn(raw.current), defn) ? raw.current : defnToText(defn);
  return html`<textarea rows="4" value=${shown} onInput=${e => {
    raw.current = e.currentTarget.value;
    state.setup.doc.vars[name] = textToDefn(raw.current);
    markDirty("prompts");
  }}></textarea>`;
}

function renameSet(from, e){
  const su = state.setup, to = e.currentTarget.value.trim();
  if(!(from in su.doc.vars) || to === from) return;
  if(!/^\w+$/.test(to) || (to !== from && to in su.doc.vars)){
    suErr("Set name: letters, digits, _ only – and unique");
    e.currentTarget.value = from;
    return;
  }
  su.doc.vars = renameKey(su.doc.vars, from, to);
  su.doc.prompts.forEach(x => Object.keys(x.vars || {}).forEach(k => { if(x.vars[k] === "$" + from) x.vars[k] = "$" + to; }));
  markDirty("prompts");
}
function addSet(){
  let n = "set", k = 2; while(n in state.setup.doc.vars) n = "set" + k++;
  state.setup.doc.vars[n] = [];
  markDirty("prompts");
}
function delSet(n){
  const su = state.setup;
  const users = su.doc.prompts.filter(x => Object.values(x.vars || {}).includes("$" + n));
  if(users.length) return suErr(`Set ${n} is still used by: ${users.map(x => x.id).join(", ")}`);
  delete su.doc.vars[n];
  markDirty("prompts");
}

export function VarSets(){
  const su = state.setup;
  const uses = n => su.doc.prompts.filter(p => Object.values(p.vars || {}).includes("$" + n)).map(p => p.id);
  const rows = Object.entries(su.doc.vars).map(([n, d]) => {
    const u = uses(n);
    return html`<tr key=${n}>
      <td style="width:170px"><input type="text" key=${n} defaultValue=${n} onChange=${e => renameSet(n, e)} />
        <div class="hint">${u.length ? "used by " + u.join(", ") : "unused"}</div></td>
      <td><${SetValues} name=${n} defn=${d} /></td>
      <td style="width:30px"><button class="x" onClick=${() => delSet(n)}>✕</button></td></tr>`;
  });
  return html`<div class="su-edit"><h2>Variable Sets</h2>
    <p class="hint" style="margin-top:0">Value lists shared by several prompts, e.g. emotions or age groups. One value per line, or <code>Label: inserted text</code>. In the prompt, choose them as "Set" under variables.</p>
    <table class="kv">${rows}</table><button style="margin-top:8px" onClick=${addSet}>＋ Set</button></div>`;
}
