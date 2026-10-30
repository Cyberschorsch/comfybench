import {html, useLayoutEffect, useRef} from "cb/vendor/htm-preact.js";
import {state, update, gridCfg, focusId, setFocus, mLabel} from "cb/store.js";
import {fileUrl} from "cb/api.js";
import {openLightbox} from "cb/views/lightbox.js";
import {Stars, LoraBadge, VarChips} from "cb/components/common.js";
import {normalizeGridCfg, buildGrid, setDimension, visiblePages} from "cb/views/compare-grid.js";

const avg = a => a.reduce((x, y) => x + y, 0) / a.length;

// ------------------------------------------------------------------ Model bar (focused model detail)
export function ModelBar(){
  const id = focusId();
  if(!id) return null;
  const m = state.idx.modelMap.get(id);
  const n = (state.data.generations || []).filter(g => g.status === "ok" && g.model === id && (g.files || []).length).length;
  return html`<div id="modelbar" class="on">
    <a class="back" href="#" onClick=${e => { e.preventDefault(); setFocus(""); }}>← Compare</a>
    <b>${mLabel(id)}</b><select id="msel" title="Switch model" aria-label="Switch model"
      onChange=${e => setFocus(e.target.value)}>
      ${(state.data.models || []).map(x => html`<option value=${x.id} selected=${x.id === id}>${mLabel(x.id)}</option>`)}
    </select>
    <span class="hint">${n} image${n === 1 ? "" : "s"}${m.notes ? " · " + m.notes : ""}</span>
  </div>`;
}

// ------------------------------------------------------------------ Compare controls (six roots, #ctrl-grid is the flex container)
export function CompareControls(){
  const opt = normalizeGridCfg();
  const cfg = gridCfg();
  const hidden = state.ui.hidden || [];
  const focus = focusId();
  return [
    html`<div><label>Columns </label><select id="axis" value=${cfg.axis}
      onChange=${e => { setDimension("axis", e.target.value); update(); }}>
      ${opt.axisOpts.map(([v, l]) => html`<option value=${v} selected=${v === cfg.axis}>${l}</option>`)}
    </select></div>`,
    html`<div><label>Sub-columns </label><select id="sub" value=${cfg.sub}
      onChange=${e => { setDimension("sub", e.target.value); update(); }}>
      ${opt.subOpts.map(([v, l]) => html`<option value=${v} selected=${v === cfg.sub}>${l}</option>`)}
    </select></div>`,
    html`<div><label>Seed </label><select id="seed" value=${cfg.seed}
      onChange=${e => { cfg.seed = e.target.value; update(); }}>
      ${opt.seedOpts.map(([v, l]) => html`<option value=${v} selected=${v === cfg.seed}>${l}</option>`)}
    </select></div>`,
    html`<div><label>Category </label><select id="cat" value=${cfg.cat}
      onChange=${e => { cfg.cat = e.target.value; update(); }}>
      ${opt.catOpts.map(([v, l]) => html`<option value=${v} selected=${v === cfg.cat}>${l}</option>`)}
    </select></div>`,
    html`<div><span class="chip ${state.ui.showOutdated ? "" : "off"}" title="Also show images generated with an older wording of the prompt"
      onClick=${() => { state.ui.showOutdated = !state.ui.showOutdated; update(); }}>⏳ older prompts</span></div>`,
    html`<input type="search" id="q" placeholder="Prompt or variant…" value=${cfg.q || ""}
      onInput=${e => { cfg.q = e.target.value; update(); }} />`,
    html`<div class="models" id="chips" style=${focus ? "display:none" : ""}>
      ${(state.data.models || []).map(m => html`<span class="chip ${hidden.includes(m.id) ? "off" : ""}" data-id=${m.id}
        onClick=${() => { const h = state.ui.hidden; state.ui.hidden = h.includes(m.id)
          ? h.filter(x => x !== m.id) : [...h, m.id]; update(); }}>${mLabel(m.id)}</span>`)}
    </div>`
  ];
}

// ------------------------------------------------------------------ One grid cell: image with in-cell paging over seeds, versions and (marked) outdated images
function pageImg(im, row, sc, pages, pi){
  const old = im.old ? html`<span class="ver old" title="Generated with an older wording of this prompt">old</span> ` : "";
  const pager = pages.length > 1 ? html`<div class="pager">
    <button title="Previous image" onClick=${e => { e.stopPropagation(); state.page[sc.key] = (pi - 1 + pages.length) % pages.length; update(); }}>‹</button>
    <b>${pi + 1}/${pages.length}</b><span class="pg-seed">${im.seedNote ? `seed ${im.seed}` : ""}</span>
    <button title="Next image" onClick=${e => { e.stopPropagation(); state.page[sc.key] = (pi + 1) % pages.length; update(); }}>›</button>
  </div>` : "";
  return [html`<img loading="lazy" src=${im.src} alt=""
    onClick=${() => openLightbox(im.id, row.ids)} />`,
    html`<div class="below">${Stars({id: im.id})}<span>${im.note ? "📝 " : ""}${LoraBadge({g: {lora_combo: im.lora || "none"}})}
      ${old}${!old && im.n > 1 ? html`<span class="ver" title="${im.n} current versions">v${im.n}</span> ` : ""}
      ${im.seconds != null ? im.seconds : "–"}s${im.seedNote && pages.length <= 1 ? ` · ${im.seed}` : ""}</span></div>`, pager];
}

function renderCell(sc, row){
  if(sc.kind === "missing"){
    return html`<div class="missing${sc.err ? " err" : ""}" title=${sc.title || ""}>${sc.text}${sc.small ? html`<br /><small>${sc.small}</small>` : ""}</div>`;
  }
  const pages = visiblePages(sc);
  if(!pages.length){   // only outdated images exist and are hidden: compact placeholder
    return html`<div class="missing stale" title="Generated with an older wording of this prompt"
      onClick=${() => { state.page["stale:" + sc.key] = true; update(); }}>⏳ older prompt version (${sc.nOld})<br /><small>click to show</small></div>`;
  }
  let pi = state.page[sc.key] || 0;
  if(pi >= pages.length) pi = 0;   // clamp after data changes
  const im = pages[pi];
  if(im.kind === "err"){
    const pager = pages.length > 1 ? html`<div class="pager">
      <button title="Previous image" onClick=${e => { e.stopPropagation(); state.page[sc.key] = (pi - 1 + pages.length) % pages.length; update(); }}>‹</button>
      <b>${pi + 1}/${pages.length}</b>
      <button title="Next image" onClick=${e => { e.stopPropagation(); state.page[sc.key] = (pi + 1) % pages.length; update(); }}>›</button>
    </div>` : "";
    return [html`<div class="missing err" title=${im.title || ""}>${im.text}${im.small ? html`<br /><small>${im.small}</small>` : ""}</div>`, pager];
  }
  return pageImg(im, row, sc, pages, pi);
}

// ------------------------------------------------------------------ Compare view (returns [modelbar, wrap])
export function Compare(){
  normalizeGridCfg();
  const focus = focusId();
  const {empty, cols, subs, rows, flatStats, sub, axis, seedNote} = buildGrid();
  const table = useRef();

  useLayoutEffect(() => {
    if(table.current && table.current.tHead && table.current.tHead.rows[0]){
      table.current.style.setProperty("--h1", table.current.tHead.rows[0].getBoundingClientRect().height + "px");
    } else if(table.current){
      table.current.style.removeProperty("--h1");
    }
  });

  if(empty){
    return [html`<${ModelBar} />`,
      html`<div class="wrap"><table id="grid" ref=${table}><tr><td class="empty">${empty}</td></tr></table></div>`];
  }

  const head1 = [], head2 = [];
  cols.forEach((c, ci) => {
    const start = cols.slice(0, ci).reduce((n, x, i) => n + subs[i].length, 0);
    const grp = flatStats.slice(start, start + subs[ci].length);
    const secs = grp.flatMap(st => st.secs);
    const metaParts = axis === "model" ? [c.m.id] : [];
    if(secs.length) metaParts.push(avg(secs).toFixed(1) + "s Ø");
    const meta = metaParts.join(" · ");
    const label = axis === "model"
      ? html`<a class="mlink" href=${"#model/" + encodeURIComponent(c.m.id)}
        onClick=${e => { e.preventDefault(); setFocus(c.m.id); }}>${c.label}</a>`
      : c.label;
    if(sub === "none"){
      head1.push(html`<th key=${"h-" + ci}>${label}${meta ? html`<span class="meta">${meta}</span>` : ""}</th>`);
    } else {
      head1.push(html`<th class="g1" key=${"g1-" + ci} colspan=${subs[ci].length}>
        ${label}${meta ? html`<span class="meta">${meta}</span>` : ""}</th>`);
      subs[ci].forEach((sv, si) => {
        const st = flatStats[start + si];
        const m2 = st.secs.length ? avg(st.secs).toFixed(1) + "s" : "";
        head2.push(html`<th class="g2" key=${"g2-" + ci + "-" + si} title=${sv.full || sv.label}>
          ${sv.label}${m2 ? html`<span class="meta">${m2}</span>` : ""}</th>`);
      });
    }
  });

  const foot = flatStats.map(st => st.ratings.length
    ? html`<td>${avg(st.ratings).toFixed(2)} ★ <span style="font-weight:400;color:var(--muted)">(${st.ratings.length})</span></td>`
    : html`<td>–</td>`);

  const body = rows.map(row => {
    const {p, gr, first} = row;
    const headV = first ? html`<div><span class="pid">${p.id}</span>${p.category ? html`<span class="tag">${p.category}</span>` : ""}
      ${p.mode !== "txt2img" && p.mode !== p.category ? html`<span class="tag">${p.mode}</span>` : ""}</div>
      <div class="txt">${p.prompt}</div>${p.check ? html`<div class="check">🔎 ${p.check}</div>` : ""}` : "";
    const chips = [];
    if(row.m && !focus) chips.push(html`<span class="var"><b>Model</b> ${mLabel(row.m.id)}</span>`);
    chips.push(...VarChips({v: gr.rest}));
    const inp = row.input ? html`<div class="inthumb">${row.input.map(([k, f]) => html`<img src=${fileUrl(f)} alt="" title=${k} />`)}<span>Input</span></div>` : "";
    const cells = row.cells.flat().map((sc, ci) => html`<td class="c" key=${"c-" + ci}>${renderCell(sc, row)}</td>`);
    return html`<tr class=${first ? "first" : ""} key=${p.id + "|" + (gr.label || "") + "|" + (row.m ? row.m.id : "")}>
      <td class="p">${headV}${chips.length ? html`<div class="vars">${chips}</div>` : ""}${inp}</td>${cells}</tr>`;
  });

  return [html`<${ModelBar} />`,
    html`<div class="wrap"><table id="grid" ref=${table}>
      <thead><tr><th rowspan=${sub === "none" ? 1 : 2}>Prompt</th>${head1}</tr>
      ${head2.length ? html`<tr>${head2}</tr>` : null}</thead>
      <tbody>${body}</tbody>
      <tfoot><tr><td>Average rating (visible)</td>${foot}</tr></tfoot>
    </table></div>`];
}
