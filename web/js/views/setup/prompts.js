import {html, useRef} from "cb/vendor/htm-preact.js";
import {state, update} from "cb/store.js";
import {uiConfirm, uiPrompt} from "cb/components/dialog.js";
import {nameRule, RE_NAME, RE_ID, clone, renameKey} from "cb/util.js";
import {VarChips} from "cb/components/common.js";
import {Field, KvTable} from "cb/views/setup/field.js";
import {expandJS, renderVarsJS, defnToText, textToDefn, promptModeJS, modesOf, markDirty, suErr, uploadInput} from "cb/views/setup/logic.js";
import {showTemplateModal, saveCurrentAsTemplate, showTemplateManager} from "cb/views/setup/templates.js";

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ------------------------------------------------------------------ preview (:1483-1497)
export function Preview({p}){
  const su = state.setup;
  const vs = expandJS(p);
  if(!vs) return html`<span class="warnline">Variables incomplete (empty list, missing set, or lists of different lengths in zip).</span>`;
  const mode = promptModeJS(p);
  const models = Object.values(su.cfg.models).filter(m => m.enabled !== false && modesOf(m).includes(mode)).length;
  const seeds = (p.seeds || su.cfg.seeds || [42]).length;
  const used = new Set([...String(p.prompt || "").matchAll(/(?<!\{)\{(\w+)\}(?!\})/g)].map(x => x[1]));
  const undef = [...used].filter(n => !(n in (p.vars || {})));
  const notInText = Object.keys(p.vars || {}).filter(n => !used.has(n) && !String(p.negative || "").includes(`{${n}}`));
  return html`<b>${vs.length} variant${vs.length === 1 ? "" : "s"}</b> × ${seeds} Seed${seeds === 1 ? "" : "s"} × ${models} model${models === 1 ? "" : "s"} (${mode}) = <b>${vs.length * seeds * models} images</b>
    ${undef.length ? html`<div class="warnline">In the text but not defined: ${undef.map(n => `{${n}}`).join(", ")}</div>` : ""}
    ${notInText.length ? html`<div class="hint">Not in the text: ${notInText.join(", ")} – it only applies if a workflow value has that name.</div>` : ""}
    ${vs.slice(0, 4).map(v => {
      const chips = Object.keys(v).length ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, x.label])) : null;
      return html`<div>${chips ? html`<${VarChips} v=${chips} /><br/>` : ""}<span class="hint">${renderVarsJS(p.prompt, v).slice(0, 260)}</span></div>`;
    })}
    ${vs.length > 4 ? html`<div class="hint">… and ${vs.length - 4} more</div>` : ""}`;
}

// ------------------------------------------------------------------ variable values (:1454, raw text like SetValues)
function VarValues({p, name, defn}){
  const raw = useRef(null);
  const shown = raw.current !== null && same(textToDefn(raw.current), defn) ? raw.current : defnToText(defn);
  return html`<textarea rows="3" value=${shown} placeholder="one value per line – or Label: inserted text" onInput=${e => {
    raw.current = e.currentTarget.value;
    p.vars[name] = textToDefn(raw.current);
    markDirty("prompts");
  }}></textarea>`;
}

// ------------------------------------------------------------------ prompt actions (:1758-1771)
async function addPrompt(){
  const su = state.setup, cur = su.doc.prompts.find(x => x.id === su.prompt);
  const id = await uiPrompt("ID of the new prompt", {placeholder: "e.g. hands-piano", ok: "Add prompt",
    validate: nameRule(RE_ID, "Letters, digits, - _ .", v => su.doc.prompts.some(x => x.id === v))});
  if(!id) return;
  su.doc.prompts.push({id, category: cur ? cur.category : "", prompt: ""});
  su.prompt = id; markDirty("prompts");
}
function dupPrompt(p){
  const su = state.setup;
  let id = p.id + "-copy", n = 2; while(su.doc.prompts.some(x => x.id === id)) id = `${p.id}-copy${n++}`;
  const c = clone(p); c.id = id;
  su.doc.prompts.splice(su.doc.prompts.indexOf(p) + 1, 0, c);
  su.prompt = id; markDirty("prompts");
}
async function delPrompt(p){
  const su = state.setup;
  if(!await uiConfirm("Generated images stay in the gallery.", {title: `Delete prompt ${p.id}?`, ok: "Delete prompt", danger: true})) return;
  const i = su.doc.prompts.indexOf(p);
  su.doc.prompts.splice(i, 1);
  su.prompt = (su.doc.prompts[i] || su.doc.prompts[i - 1] || {}).id;
  markDirty("prompts");
}
function addVar(p){
  p.vars = p.vars || {};
  let n = "var", k = 2; while(n in p.vars) n = "var" + k++;
  p.vars[n] = []; markDirty("prompts");
}
function delVar(p, name){
  delete p.vars[name];
  if(!Object.keys(p.vars).length){ delete p.vars; delete p.vary; }
  markDirty("prompts");
}
async function promptImage(p, e){
  let f = e.currentTarget.value;
  if(f === "__upload"){ f = await uploadInput(); if(!f){ e.currentTarget.value = p.image || ""; return; } }
  if(f) p.image = f; else delete p.image;
  markDirty("prompts");
}

// ------------------------------------------------------------------ controlled renames (:1664-1681)
function renamePrompt(p, e){
  const su = state.setup, nid = e.currentTarget.value.trim();
  if(!RE_ID.test(nid)){ suErr("ID: letters, digits, - _ . only"); e.currentTarget.value = p.id; return; }
  if(nid === p.id) return;
  if(su.doc.prompts.some(x => x.id === nid)){ suErr(`ID ${nid} already exists`); e.currentTarget.value = p.id; return; }
  p.id = nid; su.prompt = nid; markDirty("prompts");
}
function renameVar(p, from, e){
  const to = e.currentTarget.value.trim();
  if(!p.vars || !(from in p.vars) || to === from) return;
  if(!RE_NAME.test(to) || (to !== from && to in p.vars)){ suErr("Variable name: letters, digits, _ only – and unique"); e.currentTarget.value = from; return; }
  p.vars = renameKey(p.vars, from, to); markDirty("prompts");
}

// ------------------------------------------------------------------ editor (:1440-1482)
function PromptEdit({p}){
  const su = state.setup;
  const i = su.doc.prompts.indexOf(p);
  const P = k => ["prompts", i, k];
  const cats = [...new Set(su.doc.prompts.map(x => x.category).filter(Boolean))];
  const allModes = [...new Set(Object.values(su.cfg.models).flatMap(modesOf))];
  const ownNeg = "negative" in p;
  const setNames = Object.keys(su.doc.vars);
  const varRows = Object.entries(p.vars || {}).map(([n, d]) => {
    const isRef = typeof d === "string" && d.startsWith("$");
    return html`<tr key=${n}>
      <td style="width:150px"><input type="text" key=${n} defaultValue=${n} onChange=${e => renameVar(p, n, e)} /></td>
      <td><select value=${isRef ? d : ""} style="margin-bottom:4px" onChange=${e => { p.vars[n] = e.currentTarget.value || []; markDirty("prompts"); }}>
          <option value="">own values</option>
          ${setNames.map(sn => html`<option value=${"$" + sn}>Set: ${sn}</option>`)}
        </select>
        ${isRef ? html`<div class="hint">${defnToText(su.doc.vars[d.slice(1)] || []).split("\n").join(" · ")}</div>`
                : html`<${VarValues} p=${p} name=${n} defn=${d} />`}</td>
      <td style="width:30px"><button class="x" onClick=${() => delVar(p, n)}>✕</button></td></tr>`;
  });
  return html`<h2>${p.id}</h2>
    <div class="su-grid">
      <label>ID</label><div><input type="text" key=${p.id} defaultValue=${p.id} onChange=${e => renamePrompt(p, e)} />
        <div class="hint">Renaming detaches the prompt from images already generated (they stay in the gallery).</div></div>
      <label>Category</label><${Field} root="doc" path=${P("category")} list="su-cats" /><datalist id="su-cats">${cats.map(c => html`<option>${c}</option>`)}</datalist>
      <label>Prompt</label><${Field} root="doc" path=${P("prompt")} type="keep" as="textarea" rows="5" />
      <label>Negative</label><div><select value=${ownNeg ? "own" : "model"} style="width:auto;margin-bottom:4px" onChange=${e => {
          if(e.currentTarget.value === "own") p.negative = p.negative ?? ""; else delete p.negative;
          markDirty("prompts");
        }}><option value="model">Model default</option><option value="own">own (may be empty)</option></select>
        ${ownNeg ? html`<${Field} root="doc" path=${P("negative")} type="keep" as="textarea" rows="2" />` : ""}</div>
      <label>What to check</label><${Field} root="doc" path=${P("check")} />
      <label>Size</label><div class="row"><${Field} root="doc" path=${P("width")} type="scalar" class="short" placeholder="Width" />×<${Field} root="doc" path=${P("height")} type="scalar" class="short" placeholder="Height" /><span class="hint">empty = default</span></div>
      <label>Seeds</label><div class="row"><${Field} root="doc" path=${P("seeds")} type="seeds" style="max-width:260px" placeholder=${"Default: " + (su.cfg.seeds || [42]).join(", ")} /></div>
      <label>Input image</label><div class="row"><select value=${p.image || ""} style="max-width:320px" onChange=${e => promptImage(p, e)}>
          <option value="">– no image (txt2img) –</option>
          ${su.data.files.inputs.map(f => html`<option value=${f}>${f}</option>`)}
          <option value="__upload">Upload …</option>
        </select>${p.image ? html`<img class="thumb" src=${"inputs/" + encodeURIComponent(p.image)} alt="" />` : ""}<span class="hint">with an image the img2img workflow runs</span></div>
      <label>Mode</label><select value=${p.mode || ""} style="max-width:200px" onChange=${e => {
          const v = e.currentTarget.value; if(v) p.mode = v; else delete p.mode; markDirty("prompts");
        }}><option value="">automatic (${promptModeJS({...p, mode: undefined})})</option>${allModes.map(x => html`<option value=${x}>${x}</option>`)}</select>
    </div>
    <h3>Variables</h3>
    <p class="hint" style="margin-top:0">Use it in the prompt as <code>{name}</code>. If a variable is named like a workflow value (cfg, steps, denoise, enhance …), it controls the workflow directly.</p>
    <table class="kv">${varRows}</table>
    <div class="row" style="margin-top:6px"><button onClick=${() => addVar(p)}>＋ Variable</button>
      <label>Combine</label><select value=${p.vary || ""} style="width:auto" onChange=${e => {
        const v = e.currentTarget.value; if(v) p.vary = v; else delete p.vary; markDirty("prompts");
      }}><option value="">all combinations (product)</option><option value="zip">pairwise (zip)</option><option value="single">one at a time from base (single)</option></select></div>
    <div class="prev"><${Preview} p=${p} /></div>
    <h3>Parameter</h3>
    <p class="hint" style="margin-top:0">Apply to this prompt in all models, e.g. denoise or steps.</p>
    <${KvTable} root="doc" path=${P("params")} obj=${p.params || {}} />
    ${p.model_params ? html`<p class="hint">Model-specific parameters (model_params) are kept and can be edited in the prompts.yaml.</p>` : ""}
    <div class="row" style="margin-top:14px;justify-content:space-between"><button onClick=${() => dupPrompt(p)}>Duplicate</button><button class="x" style="font-size:13px" onClick=${() => delPrompt(p)}>Delete prompt</button></div>`;
}

// ------------------------------------------------------------------ list (:1418-1439)
export function Prompts(){
  const su = state.setup;
  const q = su.pq.trim().toLowerCase();
  const groups = {};
  su.doc.prompts.forEach(p => {
    if(q && !(p.id + " " + (p.category || "") + " " + p.prompt).toLowerCase().includes(q)) return;
    (groups[p.category || "uncategorized"] = groups[p.category || "uncategorized"] || []).push(p);
  });
  const items = [];
  Object.entries(groups).forEach(([c, ps]) => {
    items.push(html`<div class="grp" key=${"g:" + c}>${c}</div>`);
    ps.forEach(p => {
      const n = (expandJS(p) || []).length;
      items.push(html`<button key=${p.id} class=${su.prompt === p.id ? "on" : ""} onClick=${() => { su.prompt = p.id; update(); }}>${p.id}${n > 1 ? html` <span class="hint">×${n}</span>` : ""}</button>`);
    });
  });
  const p = su.doc.prompts.find(x => x.id === su.prompt);
  return html`<div class="su-cols">
    <div class="su-list">
      <input type="search" placeholder="Search…" value=${su.pq} style="margin-bottom:6px" onInput=${e => { su.pq = e.currentTarget.value; update(); }} />
      ${items}
      <div class="row" style="margin-top:8px;gap:4px">
        <button style="flex:1" onClick=${addPrompt}>＋ Prompt</button>
        <button title="Create prompt from template" style="width:32px" onClick=${() => showTemplateModal()}>📋</button>
      </div>
      <div class="row" style="margin-top:4px;gap:4px">
        <button disabled=${!p} title="Save current prompt as template" style="flex:1" onClick=${() => saveCurrentAsTemplate(p)}>Save as template</button>
        <button title="Manage templates" style="width:32px" onClick=${() => showTemplateManager()}>⚙</button>
      </div>
    </div>
    <div class="su-edit">${p ? html`<${PromptEdit} key=${p.id} p=${p} />` : html`<p class="hint">No prompt selected.</p>`}</div>
  </div>`;
}
