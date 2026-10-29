import {api} from "cb/api.js";

const STORAGE = "cb2:state";

export const state = {
  data: null,
  ui: null,            // filled below (defaults merged with stored cb2:state)
  setup: {
    data: null, cfg: null, doc: null, dirty: {config: false, prompts: false},
    sec: "models", model: null, mode: "txt2img", prompt: null, analysis: {},
    check: null, error: "", info: "", nodeQ: "", allNodes: false, pq: "", varDlg: null
  },
  run: { status: null, plan: null, planErr: "", lastDone: -1, lastReload: 0, swq: {}, imgKeys: {} },
  comfy: { data: null, loading: null },
  lb: { open: false, list: [], idx: 0 },
  idx: { byCell: null, byId: null, modelMap: null, promptMap: null, loraCombos: [] },
  isStatic: false,
};

function loadUi(){
  try {
    const v = JSON.parse(localStorage.getItem(STORAGE));
    return v && typeof v === "object" && !Array.isArray(v) ? v : null;
  } catch(e){ return null; }
}
state.ui = Object.assign({
  view: "grid", axis: "model", sub: "none", seed: "", cat: "", q: "", size: 280,
  hidden: [], gModel: "", gPrompt: "", gMin: "0", gSort: "new", gq: "", focus: "", mview: {}
}, loadUi() || {});

let renderCb = null;
let scheduled = false;

export function update(fn){
  if(fn) fn(state);
  try { localStorage.setItem(STORAGE, JSON.stringify(state.ui)); } catch(e){}
  if(!scheduled && renderCb){
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; renderCb(); });
  }
}

export function onRender(cb){ renderCb = cb; }

export function setData(d){
  state.data = d;
  index();
}

export async function reloadData(){
  if(state.isStatic) return;
  const d = await api("GET", "api/data");
  setData(d);
  update();
}

// ------------------------------------------------------------------ index / lookups

export function index(){
  const byCell = new Map(), byId = new Map();
  const modelMap = new Map((state.data.models || []).map(m => [m.id, m]));
  const promptMap = new Map((state.data.prompts || []).map(p => [p.id, p]));
  (state.data.generations || []).forEach((g, i) => {
    g._i = i;
    byId.set(g.id, g);
    const k = ck(g.model, g.prompt, g.variant_key || "", g.seed);
    if(!byCell.has(k)) byCell.set(k, []);
    byCell.get(k).push(g);
    if(loraKey(g) !== "none"){
      const kl = ckL(g.model, g.prompt, g.variant_key || "", g.seed, loraKey(g));
      if(!byCell.has(kl)) byCell.set(kl, []);
      byCell.get(kl).push(g);
    }
  });
  for(const a of byCell.values()) a.sort((x, y) => y._i - x._i);
  state.idx.byCell = byCell;
  state.idx.byId = byId;
  state.idx.modelMap = modelMap;
  state.idx.promptMap = promptMap;
  state.idx.loraCombos = [...new Set((state.data.generations || []).map(loraKey))]
    .sort((a, b) => (a === "none" ? -1 : b === "none" ? 1 : a.localeCompare(b)));
}

export function ck(m, p, v, s){ return [m, p, v, s].join("\u0001"); }
export function ckL(m, p, v, s, l){ return ck(m, p, v, s) + "\u0002" + (l || "none"); }
export function loraKey(g){ return g.lora_combo || "none"; }
export function loraLabel(k){ return (!k || k === "none") ? "base" : k; }
export function modelUsesLora(m, key){
  return key === "none" || key.split(",").map(x => x.split(":")[0].trim())
    .every(n => Object.keys(m.loras || {}).includes(n));
}
export function versionsOf(g){
  return ((state.idx.byCell.get(ck(g.model, g.prompt, g.variant_key || "", g.seed)) || []))
    .filter(x => x.status === "ok");
}
export function rating(id){ return (state.data.ratings[id] || {}).rating || 0; }
export function mLabel(id){
  if(state.idx.modelMap.has(id)) return state.idx.modelMap.get(id).label;
  if(String(id).startsWith("import:")) return id.slice(7) + " · Import";
  return id;
}
export function focusId(){
  return (state.ui.focus && state.idx.modelMap.has(state.ui.focus)) ? state.ui.focus : "";
}
export function gridCfg(){
  const id = focusId();
  if(!id) return state.ui;
  if(!state.ui.mview[id]) state.ui.mview[id] = {};
  return state.ui.mview[id];
}
export function sweepVarsFor(mid){
  const out = [];
  for(const g of state.data.generations || []){
    if(g.status !== "ok" || (mid && g.model !== mid)) continue;
    const p = state.idx.promptMap.get(g.prompt); if(!p) continue;
    const base = new Set();
    p.variants.forEach(v => { if(!v.extra) Object.keys(v.variant || {}).forEach(k => base.add(k)); });
    Object.keys(g.variant || {}).forEach(k => { if(!base.has(k) && !out.includes(k)) out.push(k); });
  }
  return out;
}
export function hashFocus(){
  const m = (location.hash || "").match(/^#model\/([^/]+)$/);
  if(!m) return "";
  let id = m[1];
  try { id = decodeURIComponent(id); } catch(e){}
  return state.idx.modelMap.has(id) ? id : "";
}
export function setFocus(id, keepHistory){
  state.ui.focus = id && state.idx.modelMap.has(id) ? id : "";
  update();
  const h = state.ui.focus ? "#model/" + encodeURIComponent(state.ui.focus) : "";
  if(!keepHistory && location.hash !== h){
    if(state.ui.focus) history.pushState(null, "", h);
    else history.pushState(null, "", location.pathname + location.search);
  }
  if(state.ui.view !== "grid" && state.ui.view !== "setup" && state.ui.view !== "run") state.ui.view = "grid";
  update();
}

// ------------------------------------------------------------------ ComfyUI options

export function loadComfyOptions(refresh){
  if(state.comfy.loading && !refresh) return state.comfy.loading;
  state.comfy.loading = api("GET", "api/comfy/options" + (refresh ? "?refresh=1" : ""))
    .catch(e => ({ok: false, error: e.message}))
    .then(d => { state.comfy.data = d; state.comfy.loading = null; update(); return d; });
  return state.comfy.loading;
}
export const comfyOpts = (mid, key) =>
  (state.comfy.data && state.comfy.data.ok && (state.comfy.data.models[mid] || {})[key]) || null;
export const comfyLoras = () => (state.comfy.data && state.comfy.data.ok && state.comfy.data.loras) || null;
