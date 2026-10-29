export function parseScalar(t){
  const s = String(t).trim();
  if(s === "") return undefined;
  if(s === "true") return true;
  if(s === "false") return false;
  if(/^-?\d+$/.test(s)) return parseInt(s, 10);
  if(/^-?\d*\.\d+$/.test(s)) return parseFloat(s);
  return String(t).includes("\n") ? t : s;
}
export const showScalar = v => v === undefined || v === null ? "" : String(v);
export function getAt(root, path){ return path.reduce((o, k) => o == null ? undefined : o[k], root); }
export function setAt(root, path, v){
  let o = root;
  for(let i = 0; i < path.length - 1; i++){
    if(o[path[i]] == null || typeof o[path[i]] !== "object") o[path[i]] = {};
    o = o[path[i]];
  }
  const last = path[path.length - 1];
  if(v === undefined) delete o[last]; else o[last] = v;
}
export const clone = o => JSON.parse(JSON.stringify(o));
export function shortVal(v){
  return String(v).split("/").pop().replace(/\.(safetensors|ckpt|pt|gguf|sft)$/i, "");
}
export function dimLabel(n){ return n === "checkpoint" ? "Checkpoints" : "Variable: " + n; }
export function keyOf(obj){ return Object.keys(obj).sort().map(k => k + "=" + obj[k]).join("|"); }
export function splitVals(s){ return String(s || "").split(",").map(x => x.trim()).filter(Boolean); }
export function asList(t){ return Array.isArray(t) ? t : (t ? [t] : []); }
export const renameKey = (obj, from, to) =>
  Object.fromEntries(Object.entries(obj).map(([k, v]) => [k === from ? to : k, v]));
export const nameRule = (re, what, taken) =>
  v => !re.test(v) ? `${what} only` : taken && taken(v) ? `"${v}" already exists` : "";
export const RE_NAME = /^\w+$/;
export const RE_ID = /^[\w.-]+$/;
export const IMAGE_KEY = /^(image|mask)\w*$/;
export const LABEL_LINE = /^([\wäöüÄÖÜß .+\-]{1,30}):\s+(.+)$/;
export function fmtDur(s){ s = Math.max(0, Math.round(s)); const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60;
  return h ? `${h} h ${m} min` : m ? `${m} min ${String(x).padStart(2,"0")} s` : `${x} s`; }
