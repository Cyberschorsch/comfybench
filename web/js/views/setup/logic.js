import {api, pickFile, readDataUrl} from "cb/api.js";
import {uiConfirm} from "cb/components/dialog.js";
import {state, update} from "cb/store.js";
import {parseScalar, showScalar, asList, IMAGE_KEY, LABEL_LINE} from "cb/util.js";

// ------------------------------------------------------------------ state helpers
// root name ("cfg"/"doc") -> the mutable object in state.setup
export const rootOf = name => name === "cfg" ? state.setup.cfg : state.setup.doc;
export const suWhich = root => root === "cfg" ? "config" : "prompts";
export function markDirty(which){
  state.setup.dirty[which] = true;
  state.setup.info = ""; state.setup.error = "";
  update();
}
export function suErr(msg){
  state.setup.error = msg; state.setup.info = "";
  update();
}
export const curModel = () => state.setup.cfg.models[state.setup.model];
export const curPrompt = () => state.setup.doc.prompts.find(x => x.id === state.setup.prompt);

// ------------------------------------------------------------------ workflow specs (:1180-1206)
export function specOf(m, mode){
  const raw = mode === "txt2img" ? (m.workflow || (m.workflows || {}).txt2img) : (m.workflows || {})[mode];
  if(!raw) return null;
  return typeof raw === "string" ? {file: raw, map: {}} : {file: raw.file || "", map: raw.map || {}};
}
export function setSpec(m, mode, spec){
  const val = spec ? (Object.keys(spec.map || {}).length ? {file: spec.file, map: spec.map} : spec.file) : undefined;
  if(mode === "txt2img"){
    if(m.workflows) delete m.workflows.txt2img;
    if(val === undefined) delete m.workflow; else m.workflow = val;
  } else {
    m.workflows = m.workflows || {};
    if(val === undefined) delete m.workflows[mode]; else m.workflows[mode] = val;
  }
  if(m.workflows && !Object.keys(m.workflows).length) delete m.workflows;
}
export function modesOf(m){
  const out = [];
  if(m.workflow || (m.workflows || {}).txt2img) out.push("txt2img");
  Object.keys(m.workflows || {}).forEach(k => { if(!out.includes(k)) out.push(k); });
  return out;
}
export function hasValue(m, key){
  if((state.setup.data.builtin_keys || []).includes(key) || IMAGE_KEY.test(key)) return true;
  return key in (state.setup.cfg.defaults || {}) || key in (m.params || {});
}

// ------------------------------------------------------------------ prompt vars (:1382-1416)
export function normVar(d){
  if(typeof d === "string" && d.startsWith("$")){ d = state.setup.doc.vars[d.slice(1)]; if(d === undefined) return null; }
  if(Array.isArray(d)) return d.map(v => [String(v), v]);
  if(d && typeof d === "object") return Object.entries(d);
  return [[String(d), d]];
}
export function expandJS(p){
  const vars = p.vars || {}, names = Object.keys(vars);
  if(!names.length) return [{}];
  const opts = names.map(n => normVar(vars[n]));
  if(opts.some(o => !o || !o.length)) return null;
  let combos = [];
  const mode = p.vary || "product";
  if(mode === "zip"){
    const L = opts[0].length;
    if(opts.some(o => o.length !== L)) return null;
    for(let i = 0; i < L; i++) combos.push(opts.map(o => o[i]));
  } else if(mode === "single"){
    const base = opts.map(o => o[0]);
    combos.push(base);
    opts.forEach((o, i) => o.slice(1).forEach(a => { const c = base.slice(); c[i] = a; combos.push(c); }));
  } else {
    combos = [[]];
    opts.forEach(o => { combos = combos.flatMap(c => o.map(a => [...c, a])); });
  }
  return combos.map(c => Object.fromEntries(names.map((n, i) => [n, {label: c[i][0], value: c[i][1]}])));
}
export const renderVarsJS = (t, v) =>
  String(t || "").replace(/(?<!\{)\{(\w+)\}(?!\})/g, (m, n) => n in v ? String(v[n].value) : m);
export function defnToText(d){
  if(Array.isArray(d)) return d.map(showScalar).join("\n");
  if(d && typeof d === "object") return Object.entries(d).map(([k, v]) => `${k}: ${v}`).join("\n");
  return showScalar(d);
}
export function textToDefn(t){
  const lines = t.split("\n").map(l => l.trim()).filter(Boolean);
  if(!lines.length) return [];
  if(lines.every(l => LABEL_LINE.test(l))){ const o = {}; lines.forEach(l => { const x = l.match(LABEL_LINE); o[x[1].trim()] = x[2]; }); return o; }
  return lines.map(parseScalar);
}
export function promptModeJS(p){
  if(p.mode) return p.mode;
  const keys = [...Object.keys(p), ...Object.keys(p.vars || {}), ...Object.keys(p.params || {})];
  return keys.some(k => IMAGE_KEY.test(k)) ? "img2img" : "txt2img";
}

// ------------------------------------------------------------------ suggestions (:1349-1379)
export function applySuggestions(m, mode, onlyIfEmpty){
  const sp = specOf(m, mode); const an = state.setup.analysis[sp.file];
  if(!an || an.error) return false;
  if(onlyIfEmpty && Object.keys(sp.map).length) return false;
  const taken = new Set(Object.values(sp.map).flatMap(asList));
  for(const [k, targets] of Object.entries(an.suggest || {})){
    for(const t of targets){
      if(taken.has(t)) continue;
      sp.map[k] = [...asList(sp.map[k]), t]; taken.add(t);
    }
  }
  setSpec(m, mode, sp);
  fillFromWorkflow(m, mode, Object.keys(an.suggest || {}));
  return true;
}
export function fillFromWorkflow(m, mode, keys){
  // Adopt values the model itself needs (sampler, steps, model negative …) as parameters
  const sp = specOf(m, mode); const an = state.setup.analysis[sp.file]; if(!an || an.error) return;
  const defaults = state.setup.cfg.defaults || {};
  const skip = new Set(["prompt", "seed", "width", "height", "image"]);
  const nodeVal = t => { const [nid, ...rest] = t.split("/"); const inp = rest.join("/");
    const n = an.nodes.find(x => x.id === nid); const i = n && n.inputs.find(x => x.name === inp);
    return i && !i.link ? i.value : undefined; };
  for(const k of keys){
    if(skip.has(k) || (m.params && k in m.params)) continue;
    const v = (an.values && k in an.values) ? an.values[k] : nodeVal(asList(sp.map[k])[0] || "");
    if(v === undefined || v === null || (typeof v === "string" && !v.trim())) continue;
    if(k in defaults && defaults[k] === v) continue;
    m.params = m.params || {}; m.params[k] = v;
  }
}

// ------------------------------------------------------------------ uploads (:1153-1177)
export async function uploadWorkflow(){
  const f = await pickFile(".json,application/json"); if(!f) return null;
  let wf; try { wf = JSON.parse(await f.text()); } catch(e){ suErr("That is not a valid JSON file."); return null; }
  const send = overwrite => api("POST", "api/upload", {kind: "workflow", name: f.name, workflow: wf, overwrite});
  try {
    let r;
    try { r = await send(false); }
    catch(e){ if(/already exists/.test(e.message) && await uiConfirm(e.message, {title: "Overwrite file?", ok: "Overwrite"})) r = await send(true); else throw e; }
    state.setup.data.files.workflows = [...new Set([...state.setup.data.files.workflows, r.path])].sort();
    delete state.setup.analysis[r.path];
    update();
    return r.path;
  } catch(e){ suErr(e.message); return null; }
}
export async function uploadInput(){
  const f = await pickFile("image/png,image/jpeg,image/webp"); if(!f) return null;
  const dataUrl = await readDataUrl(f);
  const send = overwrite => api("POST", "api/upload", {kind: "input", name: f.name, data: dataUrl, overwrite});
  try {
    let r;
    try { r = await send(false); }
    catch(e){ if(/already exists/.test(e.message) && await uiConfirm(e.message, {title: "Overwrite file?", ok: "Overwrite"})) r = await send(true); else throw e; }
    state.setup.data.files.inputs = [...new Set([...state.setup.data.files.inputs, r.path])].sort();
    update();
    return r.path;
  } catch(e){ suErr(e.message); return null; }
}
