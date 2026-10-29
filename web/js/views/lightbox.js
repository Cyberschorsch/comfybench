import {html, useEffect} from "cb/vendor/htm-preact.js";
import {state, update, loraKey, mLabel, versionsOf, rating as ratingOf} from "cb/store.js";
import {copy, fileUrl} from "cb/api.js";
import {toast} from "cb/components/toast.js";
import {Stars, VarChips, LoraBadge, setNote, deleteGeneration} from "cb/components/common.js";

// ------------------------------------------------------------------ Lightbox (state lives in state.lb)
export function openLightbox(id, list){
  state.lb.list = list && list.length ? list : [id];
  state.lb.idx = Math.max(0, state.lb.list.indexOf(id));
  state.lb.open = true;
  update();
}
export function closeLightbox(){
  state.lb.open = false;
  update();
}

function yamlFor(g){
  const p = state.idx.promptMap.get(g.prompt) || {};
  const id = [g.prompt === "_import" ? "new" : g.prompt, ...Object.values(g.variant || {})]
    .join("-").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  let y = `  - id: ${id}\n`;
  if(p.category) y += `    category: ${JSON.stringify(p.category)}\n`;
  y += `    prompt: ${JSON.stringify(g.text)}\n`;
  if(g.negative) y += `    negative: ${JSON.stringify(g.negative)}\n`;
  const v = g.values || {};
  if(v.width && v.height) y += `    width: ${v.width}\n    height: ${v.height}\n`;
  for(const [k, src] of Object.entries(g.input_src || {})) y += `    ${k}: ${JSON.stringify(src)}\n`;
  if(g.seed != null) y += `    seeds: [${g.seed}]\n`;
  if(g.mode === "import" && v.checkpoint) y += `    # generated with ${v.checkpoint}\n`;
  if(p.check) y += `    check: ${JSON.stringify(p.check)}\n`;
  return y;
}

function Lightbox(){
  const open = state.lb.open;
  const byId = state.idx.byId;
  const g = open && byId ? byId.get(state.lb.list[state.lb.idx] || "") : null;
  const missing = open && !g;

  useEffect(() => {
    if(!open) return;
    const onKey = e => {
      if(document.querySelector("dialog[open]")) return;
      if(e.key === "Escape"){ if(e.target.blur) e.target.blur(); closeLightbox(); return; }
      if(e.target.tagName === "TEXTAREA") return;
      if(e.key === "ArrowRight" || e.key === "ArrowLeft"){
        const list = state.lb.list, n = list.length;
        state.lb.idx = (state.lb.idx + (e.key === "ArrowRight" ? 1 : -1) + n) % n;
        update();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    if(missing) closeLightbox();
  }, [missing]);

  if(!open || missing) return html`<div id="lb" class=""></div>`;

  const p = state.idx.promptMap.get(g.prompt) || {};
  const vers = versionsOf(g);
  const r = (state.data.ratings || {})[g.id] || {};
  const mixedLora = new Set(vers.map(loraKey)).size > 1;
  const ins = Object.entries(g.inputs || {});
  const notePlaceholder = state.isStatic ? "Notes only via bench.py serve" : "Note…";

  return html`<div id="lb" class="open">
    <div class="img" onClick=${closeLightbox}>
      <img src=${fileUrl(g.files[0])} alt="" />
    </div>
    <aside>
      <h3>${mLabel(g.model)} · ${g.prompt === "_import" ? "own image" : g.prompt}</h3>
      <div class="gen-id" style="font-family:ui-monospace,monospace;font-size:11px;color:var(--muted);margin-bottom:4px;user-select:all" title="Click to copy">
        ID: <b>${g.id}</b> <button style="font-size:10px;padding:0 6px;margin-left:4px"
          onClick=${() => { copy(g.id); toast("Generation ID copied: " + g.id); }}>📋</button>
      </div>
      ${g.variant && Object.keys(g.variant).length ? html`<div class="vars">${VarChips({v: g.variant})}</div>` : ""}
      <p class="muted">${LoraBadge({g})}${g.seed != null ? `Seed ${g.seed} · ` : ""}
        ${g.seconds != null ? g.seconds + "s · " : ""}${g.mode || "txt2img"} · ${(g.at || "").replace("T", " ")}
        ${state.lb.list.length > 1 ? ` · ${state.lb.idx + 1}/${state.lb.list.length}` : ""}</p>
      ${ins.map(([k, f]) => html`<div class="inp"><img src=${fileUrl(f)} alt="" />
        <span class="muted">${k}: ${(g.input_src || {})[k] || ""}</span></div>`)}
      <div class="sec">${Stars({id: g.id})} <button class="del-btn" onClick=${() => deleteGeneration(g.id)} title="Delete">🗑️</button></div>
      <textarea id="note" rows="2" placeholder=${notePlaceholder} disabled=${state.isStatic}
        value=${r.note || ""} onInput=${e => setNote(state.lb.list[state.lb.idx], e.target.value)}></textarea>
      <div class="sec"><b>Prompt</b><span><button onClick=${() => copy(g.text)}>copy</button>
        <button onClick=${() => copy(yamlFor(g))} title="As a new entry for prompts.yaml">as YAML</button></span></div>
      <pre>${g.text}</pre>
      ${g.final_text ? html`<div class="sec"><b>Expanded by the model</b><button onClick=${() => copy(g.final_text)}>copy</button></div>
        <pre style="max-height:220px;overflow:auto">${g.final_text}</pre>` : ""}
      ${g.negative ? html`<b>Negative</b><pre>${g.negative}</pre>` : ""}
      <b>Parameter</b><pre>${JSON.stringify(g.values || {}, null, 2)}</pre>
      ${vers.length > 1 ? html`<b>Versions of this cell</b>
        <div style="display:flex;flex-wrap:wrap;gap:4px;margin:6px 0 12px">
        ${vers.slice().reverse().map((x, i) => html`<button title=${x.at}
          class=${x.id === g.id ? "on" : ""}
          onClick=${() => { state.lb.list[state.lb.idx] = x.id; update(); }}>
          v${i + 1}${mixedLora && loraKey(x) !== "none" ? ` · ${loraKey(x)}` : ""}
          ${ratingOf(x.id) ? ` ${ratingOf(x.id)}★` : ""}</button>`)}
        </div>` : ""}
      ${g.source ? html`<p class="muted">Source: ${g.source}</p>` : ""}
      ${g.files.length > 1 ? html`<p>${g.files.map((f, i) => i > 0
        ? [html`<br/>`, html`<a style="color:#fb923c" href=${fileUrl(f)} target="_blank">${f.split("/").pop()}</a>`]
        : html`<a style="color:#fb923c" href=${fileUrl(f)} target="_blank">${f.split("/").pop()}</a>`)}</p>` : ""}
      <p class="muted">Esc closes · ←/→ navigates${p.check ? html`<br/>🔎 ${p.check}` : ""}</p>
    </aside>
  </div>`;
}

export {Lightbox};
