import {html} from "cb/vendor/htm-preact.js";
import {api} from "cb/api.js";
import {toast} from "cb/components/toast.js";
import {uiConfirm} from "cb/components/dialog.js";
import {state, update, index, rating as ratingOf, loraKey} from "cb/store.js";

// ------------------------------------------------------------------ Stars (click -> setRating)
export function Stars({id}){
  const r = ratingOf(id);
  return html`<span class="stars${state.isStatic ? " ro" : ""}" data-id="${id}">
    ${[1, 2, 3, 4, 5].map(i => html`<span class="${i <= r ? "on" : ""}" data-r="${i}"
      onClick=${() => setRating(id, i)}>★</span>`)}
  </span>`;
}

// ------------------------------------------------------------------ Var chips (inserted directly into .vars flex container)
export function VarChips({v}){
  return Object.entries(v || {}).map(([k, l]) =>
    html`<span class="var"><b>${k}</b> ${l}</span>`);
}

export function LoraBadge({g}){
  const k = loraKey(g);
  if(k === "none") return null;
  return html`<span class="lora" title="LoRA combination">${k}</span>`;
}

// ------------------------------------------------------------------ OptSelect (like optSelect :853-859)
export function OptSelect({value, options = [], onChange, ...rest}){
  const c = value == null ? "" : String(value);
  const missing = c && !options.includes(c);
  return html`<select ...${rest} value=${c} onChange=${e => { if(onChange) onChange(e.target.value); }}>
    <option value="">– select –</option>
    ${missing ? html`<option value="${c}" selected>${c} ⚠ not in ComfyUI</option>` : ""}
    ${options.map(o => html`<option value="${o}" selected=${o === c}>${o}</option>`)}
  </select>`;
}

// ------------------------------------------------------------------ Ratings / note / delete (like :711-748, static copy -> toast)

export async function setRating(id, v){
  if(state.isStatic){ toast("Rating only works via bench.py serve"); return; }
  const r = Object.assign({}, state.data.ratings[id]);
  r.rating = r.rating === v ? 0 : v;
  if(!r.rating && !r.note) delete state.data.ratings[id]; else state.data.ratings[id] = r;
  update();  // optimistic UI first
  try { await api("POST", "api/rating", {id, rating: r.rating || 0, note: r.note || ""}); }
  catch(e){ toast("Save failed"); }
}

export function setNote(id, text){
  const r = Object.assign({}, state.data.ratings[id]);
  r.note = text;
  if(!r.rating && !text) delete state.data.ratings[id]; else state.data.ratings[id] = r;
  clearTimeout(noteTimer);
  noteTimer = setTimeout(async () => {
    const cur = state.data.ratings[id] || {};
    update();  // optimistic UI first
    try { await api("POST", "api/rating", {id, rating: cur.rating || 0, note: cur.note || ""}); }
    catch(e){ toast("Save failed"); }
  }, 500);
}

export async function deleteGeneration(id){
  if(state.isStatic){ toast("Deleting only works via bench.py serve"); return; }
  if(!await uiConfirm("The image file and its entry will be removed permanently.",
      {title: "Delete this image?", ok: "Delete", danger: true})) return;
  try { await api("POST", "api/delete", {id}); }
  catch(e){ toast("Delete failed"); return; }
  state.data.generations = (state.data.generations || []).filter(g => g.id !== id);
  state.idx.byId.delete(id);
  index();
  update();
  toast("Deleted");
}

let noteTimer = null;
