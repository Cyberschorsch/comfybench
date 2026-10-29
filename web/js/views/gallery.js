import {html} from "cb/vendor/htm-preact.js";
import {state, update, rating, mLabel} from "cb/store.js";
import {fileUrl} from "cb/api.js";
import {openLightbox} from "cb/views/lightbox.js";
import {Stars, VarChips, LoraBadge, deleteGeneration} from "cb/components/common.js";

const MIN_OPTS = ["0", "1", "2", "3", "4", "5"];
const MIN_LABEL = ["all", "★", "★★", "★★★", "★★★★", "★★★★★"];

// ------------------------------------------------------------------ Gallery view
// Filter / sort / 600er-Limit like report_template.html:636-652.
// Steuerelemente wie :241-245, als kontrollierte Selects/Inputs auf state.ui.
export function Gallery(){
  const d = state.data || {generations: [], ratings: {}};
  const q = state.ui.gq.trim().toLowerCase();
  const min = Number(state.ui.gMin);
  let gens = (d.generations || []).filter(g => g.status === "ok" && g.files && g.files.length
    && (!state.ui.gModel || g.model === state.ui.gModel)
    && (!state.ui.gPrompt || g.prompt === state.ui.gPrompt)
    && rating(g.id) >= min
    && (!q || [g.prompt, g.text, g.final_text || "", g.negative, g.model, mLabel(g.model),
      JSON.stringify(g.variant || {}), JSON.stringify(g.values || {}), g.lora_combo || "",
      g.source || "", (d.ratings[g.id] || {}).note || ""].join(" ").toLowerCase().includes(q)));
  if(state.ui.gSort === "best") gens.sort((a, b) => rating(b.id) - rating(a.id) || b._i - a._i);
  else if(state.ui.gSort === "old") gens.sort((a, b) => a._i - b._i);
  else gens.sort((a, b) => b._i - a._i);
  const visible = gens.slice(0, 600).map(g => g.id);
  if(!gens.length) return html`<div class="empty">No images for this filter.</div>`;
  const cards = gens.slice(0, 600).map(g => html`<figure class="card" key=${g.id}>
    <img loading="lazy" src=${fileUrl(g.files[0])} alt=""
      onClick=${() => openLightbox(g.id, visible)} />
    <figcaption>
      <b>${mLabel(g.model)}</b> · ${g.prompt === "_import" ? "own image" : g.prompt}
      ${g.mode && g.mode !== "txt2img" ? html` <span class="tag">${g.mode}</span>` : ""}
      ${g.variant && Object.keys(g.variant).length ? html`<div class="vars">${VarChips({v: g.variant})}</div>` : ""}
      <div class="below">${Stars({id: g.id})}<span>${LoraBadge({g})}
        ${g.seed != null ? `seed ${g.seed} · ` : ""}${(g.at || "").slice(0, 10)}</span></div>
      <button class="del-btn-card" onClick=${() => deleteGeneration(g.id)} title="Delete">🗑️</button>
    </figcaption>
  </figure>`);
  return html`<div id="gal">${cards}${(gens.length > 600
    ? html`<div class="empty">… ${gens.length - 600} more – refine the filter</div>` : "")}</div>`;
}

// ------------------------------------------------------------------ Gallery controls
export function GalleryControls(){
  const d = state.data || {generations: []};
  const gm = [...new Set((d.generations || []).map(g => g.model))];
  const gp = [...new Set((d.generations || []).map(g => g.prompt))].sort();
  const ui = state.ui;
  const minOpt = (o, i) => html`<option value=${o} selected=${o === ui.gMin}>${MIN_LABEL[i]}</option>`;
  return [
    html`<div><label>Model </label><select id="gModel" value=${ui.gModel}
      onChange=${e => { ui.gModel = e.target.value; update(); }}>
      <option value="">all</option>${gm.map(m => html`<option value=${m} selected=${m === ui.gModel}>${mLabel(m)}</option>`)}
    </select></div>`,
    html`<div><label>Prompt </label><select id="gPrompt" value=${ui.gPrompt}
      onChange=${e => { ui.gPrompt = e.target.value; update(); }}>
      <option value="">all</option>${gp.map(p => html`<option value=${p} selected=${p === ui.gPrompt}>${p}</option>`)}
    </select></div>`,
    html`<div><label>Min. </label><select id="gMin" value=${ui.gMin}
      onChange=${e => { ui.gMin = e.target.value; update(); }}>
      ${MIN_OPTS.map(minOpt)}
    </select></div>`,
    html`<div><label>Sort </label><select id="gSort" value=${ui.gSort}
      onChange=${e => { ui.gSort = e.target.value; update(); }}>
      <option value="new" selected=${ui.gSort === "new"}>newest</option>
      <option value="best" selected=${ui.gSort === "best"}>best</option>
      <option value="old" selected=${ui.gSort === "old"}>oldest</option>
    </select></div>`,
    html`<input type="search" id="gq" value=${ui.gq} placeholder="Text, variant, note…"
      onInput=${e => { ui.gq = e.target.value; update(); }} />`
  ];
}
