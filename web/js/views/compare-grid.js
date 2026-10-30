import {state, gridCfg, focusId, sweepVarsFor, loraKey, modelUsesLora, rating, mLabel, ck, ckL, isCurrentRev} from "cb/store.js";
import {keyOf, shortVal, dimLabel} from "cb/util.js";
import {fileUrl} from "cb/api.js";

// ------------------------------------------------------------------ Compare grid: pure calculation
// Pure calculation behind the compare grid: returns data instead of HTML.
// normalizeGridCfg() mutates gridCfg() and is idempotent.

export function normalizeGridCfg(){
  const cfg = gridCfg(), focus = focusId();
  const D = state.data || {prompts: [], seeds: [], generations: [], ratings: {}};
  const varNames = [...new Set(D.prompts.flatMap(p => p.variants.flatMap(v => Object.keys(v.variant || {}))))];
  const loraCombos = state.idx.loraCombos || [];
  if(!D.seeds.map(String).includes(String(cfg.seed)) && cfg.seed !== "all") cfg.seed = String(D.seeds[0] ?? "");
  if(!cfg.axis) cfg.axis = focus ? "" : "model";
  if(cfg.axis.startsWith("var:") && !varNames.includes(cfg.axis.slice(4))) cfg.axis = "";
  if(cfg.axis === "lora" && !loraCombos.some(k => k !== "none")) cfg.axis = "";
  if(focus && (!cfg.axis || cfg.axis === "model")){
    const sw = sweepVarsFor(focus);
    cfg.axis = sw.includes("checkpoint") ? "var:checkpoint" : sw.length ? "var:" + sw[0]
      : (loraCombos.some(k => k !== "none") ? "lora" : "seed");
  }
  if(!focus && !cfg.axis) cfg.axis = "model";
  const axisOpts = focus
    ? [...varNames.map(n => ["var:" + n, dimLabel(n)]),
       ...(loraCombos.some(k => k !== "none") ? [["lora", "LoRA"]] : []), ["seed", "Seeds"]]
    : [["model", "Models"], ["seed", "Seeds"],
       ...(loraCombos.some(k => k !== "none") ? [["lora", "LoRA"]] : []),
       ...varNames.map(n => ["var:" + n, dimLabel(n)])];
  const subOpts = [["none", "none"],
    ...(D.seeds.length > 1 ? [["seed", "Seeds"]] : []),
    ...(loraCombos.some(k => k !== "none") ? [["lora", "LoRA"]] : []),
    ...varNames.map(n => ["var:" + n, dimLabel(n)])];
  if(cfg.sub === cfg.axis) cfg.sub = "none";
  if(!cfg.sub || !subOpts.some(o => o[0] === cfg.sub)) cfg.sub = "none";
  return {
    axisOpts, subOpts,
    seedOpts: [...D.seeds.map(s => [s, s]), ["all", "all (paged)"]],
    catOpts: [["", "all"], ...[...new Set(D.prompts.map(p => p.category).filter(Boolean))].map(c => [c, c])]
  };
}

// A dimension can be columns or sub-columns, not both: picking it in the second
// dropdown swaps the two instead of hiding it from the list.
export function setDimension(kind, v){
  const cfg = gridCfg(), other = kind === "axis" ? "sub" : "axis";
  const prev = cfg[kind] || "none";
  const valid = (k, x) => k === "axis" ? !!x && x !== "none" : !!x && x !== "none" && x !== "model";
  if(v === cfg[other]) cfg[other] = valid(other, prev) ? prev : (other === "axis" ? (focusId() ? "seed" : "model") : "none");
  cfg[kind] = v;
}

function cellData(model, p, vkey, seeds, sink, lora, seedNote){
  // Pages for one grid cell: newest-first per seed, seeds in order, outdated prompt
  // revisions last (and only when shown). One page is visible at a time.
  if(!model.modes.includes(p.mode)) return {kind: "missing", text: `no ${p.mode} workflow`};
  const pages = [], oldPages = [];
  for(const s of seeds){
    const all = (state.idx.byCell.get(lora === undefined || lora === "none"
      ? ck(model.id, p.id, vkey, s) : ckL(model.id, p.id, vkey, s, lora)) || []);
    const okAll = all.filter(x => x.status === "ok");
    const okCur = okAll.filter(isCurrentRev);
    const okOld = okAll.filter(x => !isCurrentRev(x));
    if(okCur.length){
      sink.ratings.push(rating(okCur[0].id));
      if(okCur[0].seconds) sink.secs.push(okCur[0].seconds);
      if(!sink.input && okCur[0].inputs && Object.keys(okCur[0].inputs).length) sink.input = okCur[0].inputs;
      okCur.forEach((g, i) => pages.push({kind: "img", id: g.id, src: fileUrl(g.files[0]),
        seconds: g.seconds, note: (state.data.ratings[g.id] || {}).note, seed: s, seedNote,
        n: okCur.length, lora: g.lora_combo === "none" ? undefined : g.lora_combo}));
    }
    okOld.forEach(g => oldPages.push({kind: "img", id: g.id, src: fileUrl(g.files[0]),
      seconds: g.seconds, note: (state.data.ratings[g.id] || {}).note, seed: s, seedNote, old: true,
      lora: g.lora_combo === "none" ? undefined : g.lora_combo}));
    if(!okAll.length && all[0]) pages.push({kind: "err", text: `Error · seed ${s}`,
      title: all[0].error || "", small: String(all[0].error || "").slice(0, 90)});
  }
  if(!pages.length && !oldPages.length){
    return {kind: "missing", text: seeds.length > 1 ? "not generated yet" : `not generated yet · seed ${seeds[0]}`};
  }
  return {kind: "pages", pages, oldPages, nOld: okOldCount(oldPages)};
}

function okOldCount(oldPages){ return new Set(oldPages.map(x => x.id)).size; }

// Visible page list for a cell, honouring the global toggle and a per-cell reveal.
export function visiblePages(sc){
  if(sc.kind !== "pages") return [];
  if(state.ui.showOutdated || state.page["stale:" + sc.key]) return [...sc.pages, ...sc.oldPages];
  return sc.pages;
}

export function buildGrid(){
  const D = state.data || {models: [], prompts: [], generations: [], seeds: [], ratings: {}};
  const hidden = state.ui.hidden || [];
  const focus = focusId();
  const focusModel = focus ? state.idx.modelMap.get(focus) || null : null;
  const cfg = gridCfg();
  const models = D.models.filter(m => !hidden.includes(m.id) && (!focus || m.id === focus));
  const axis = cfg.axis || "model";
  const axVar = axis.startsWith("var:") ? axis.slice(4) : null;
  const sub = cfg.sub && cfg.sub !== axis ? cfg.sub : "none";
  const subVar = sub.startsWith("var:") ? sub.slice(4) : null;
  const seedsSel = cfg.seed === "all" ? D.seeds : [Number(cfg.seed)];
  const q = (cfg.q || "").trim().toLowerCase();
  const loraCombos = state.idx.loraCombos || [];

  // Columns
  let cols;
  if(axis === "model") cols = models.map(m => ({id: "m:" + m.id, label: m.label, m}));
  else if(axis === "seed") cols = D.seeds.map(s => ({id: "s:" + s, label: "Seed " + s, s}));
  else if(axis === "lora") cols = loraCombos
    .filter(k => k === "none" || D.generations.some(g => loraKey(g) === k && !hidden.includes(g.model)))
    .map(k => ({id: "l:" + k, label: k === "none" ? "base" : k, lora: k}));
  else {
    const labels = [];
    D.prompts.forEach(p => p.variants.forEach(v => {
      const l = v.variant[axVar]; if(l === undefined) return;
      if(focus && !D.generations.some(g => g.status === "ok" && g.model === focus && (g.variant || {})[axVar] === l)) return;
      if(!labels.includes(l)) labels.push(l);
    }));
    // Images that never got this value (not swept, or swept later) keep their own "default" column
    const hasDefault = D.generations.some(g => g.status === "ok" && g.mode !== "import" && (g.files || []).length
      && !hidden.includes(g.model) && (!focus || g.model === focus) && (g.variant || {})[axVar] === undefined);
    if(hasDefault && labels.length) labels.push("\u0000default");   // un-swept images last
    cols = labels.map(l => l === "\u0000default"
      ? {id: "v:default", label: "default", dflt: true}
      : {id: "v:" + l, label: shortVal(axVar) + ": " + shortVal(l), full: axVar + ": " + l, l});
  }

  // Rows: Prompt × (variant without column/sub variable) × (model, when model is not the column)
  const consumed = [axVar, subVar].filter(Boolean);
  const rows = [];
  for(const p of D.prompts){
    if(cfg.cat && p.category !== cfg.cat) continue;
    if(focus && !focusModel.modes.includes(p.mode)) continue;   // no workflow for this model
    let groups = [];
    if(consumed.length){
      const seen = new Map();
      p.variants.forEach(v => {
        const rest = Object.fromEntries(Object.entries(v.variant).filter(([k]) => !consumed.includes(k)));
        const k = keyOf(rest);
        if(!seen.has(k)){
          seen.set(k, {rest, label: Object.entries(rest).map(([a, b]) => a + ": " + b).join(" · ")}); groups.push(seen.get(k));
        }
      });
    } else groups = p.variants.map(v => ({rest: v.variant, label: v.label, key: v.key}));
    let first = true;
    for(const gr of groups){
      if(q && !(p.id + " " + gr.label + " " + p.prompt).toLowerCase().includes(q)) continue;
      const rowModels = (axis === "model" || focus) ? [null] : models;
      for(const m of rowModels){ rows.push({p, gr, m, first}); first = false; }
    }
  }
  if(!rows.length || !cols.length){
    return {empty: focus ? `Nothing generated with ${mLabel(focus)} for this filter.`
      : axVar ? `No prompts with the variable "${axVar}" for this filter.`
      : "No prompts for this filter.", cols: [], subs: [], rows: [], flatStats: [], sub, axis, seedNote: false};
  }

  // Sub-columns per main column: only values that actually have images for that column
  const modelOf = (r, c) => (axis === "model" ? c.m : null) || r.m || models[0] || focusModel;
  const subs = cols.map(c => {
    if(sub === "none") return [{}];
    if(sub === "seed") return D.seeds.map(s => ({s, label: "Seed " + s}));
    if(sub === "lora") return loraCombos.map(k => ({lora: k, label: k === "none" ? "base" : k}));
    const out = [], seen = new Set();
    let hasDefault = false;
    for(const g of D.generations){
      if(g.status !== "ok" || g.mode === "import" || !(g.files || []).length) continue;
      if(axis === "model" ? g.model !== c.m.id : (focus && g.model !== focus)) continue;
      if(axis === "seed" && g.seed !== c.s) continue;
      if(axis === "lora" && loraKey(g) !== c.lora) continue;
      if(axVar && (g.variant || {})[axVar] !== c.l) continue;
      const v = (g.variant || {})[subVar];
      if(v === undefined){ hasDefault = true; continue; }
      if(!seen.has(v)){ seen.add(v); out.push(v); }
    }
    if(hasDefault) out.push("\u0000default");   // un-swept images last
    return out.map(v => v === "\u0000default" ? {val: "", label: "default"} : {val: v, label: shortVal(v), full: v});
  });

  const seedNote = cfg.seed === "all" && axis !== "seed" && sub !== "seed";
  // A main column without any sub column has nothing to show
  const keepIdx = cols.map((_, i) => i).filter(i => subs[i].length);
  if(keepIdx.length !== cols.length){
    const nc = keepIdx.map(i => cols[i]), ns = keepIdx.map(i => subs[i]);
    cols = nc; subs.splice(0, subs.length, ...ns);
  }
  if(!cols.length) return {empty: "No images for this sub-column.", cols: [], subs: [], rows: [], flatStats: [], sub, axis, seedNote: false};
  const flatStats = cols.flatMap((c, ci) => subs[ci].map(() => ({ratings: [], secs: []})));

  const body = rows.map(({p, gr, m, first}) => {
    const sink = {ratings: [], secs: [], input: null};
    const cells = cols.map((c, ci) => {
      const model = modelOf({p, gr, m}, c);
      const vbase = {...gr.rest};
      if(axVar && !c.dflt) vbase[axVar] = c.l;
      const start = subs.slice(0, ci).reduce((n, x) => n + x.length, 0);
      const subcells = subs[ci].map((sv, si) => {
        const st = flatStats[start + si];
        if(!model) return {kind: "missing", text: "–"};
        const attribs = {...vbase};
        if(subVar && sv.val) attribs[subVar] = sv.val;
        const vkey = keyOf(attribs);
        if(!p.variants.some(v => v.key === vkey)) return {kind: "missing", text: "not in set"};
        if(axis === "lora" && !modelUsesLora(model, c.lora)) return {kind: "missing", text: "LoRA not configured for this model"};
        if(sub === "lora" && !modelUsesLora(model, sv.lora)) return {kind: "missing", text: "LoRA not configured"};
        const seeds = sub === "seed" ? [sv.s] : axis === "seed" ? [c.s] : seedsSel;
        const local = {ratings: [], secs: [], input: sink.input};
        const cellKey = [c.id, si, p.id, gr.key || gr.label || "", m ? m.id : "", focus || ""].join("\u0003");
        const sc = cellData(model, p, vkey, seeds, local,
          axis === "lora" ? c.lora : sub === "lora" ? sv.lora : undefined, seedNote);
        sc.key = cellKey;
        st.ratings.push(...local.ratings); st.secs.push(...local.secs);
        sink.input = local.input;
        return sc;
      });
      return subcells;
    });
    const ids = [];
    cells.forEach(group => group.forEach(sc => {
      if(sc.kind === "pages") [...sc.pages, ...sc.oldPages].forEach(im => { if(im.kind === "img") ids.push(im.id); });
    }));
    return {p, gr, first, m, cells, ids,
      input: sink.input ? Object.entries(sink.input) : null};
  });

  return {cols, subs, rows: body, flatStats, sub, axis, seedNote, empty: undefined};
}
