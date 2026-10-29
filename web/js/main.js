import {html, render, useLayoutEffect} from "cb/vendor/htm-preact.js";
import {state, update, onRender, setData, reloadData, hashFocus, focusId} from "cb/store.js";
import {Toast} from "cb/components/toast.js";

const root = document.getElementById("app");
let loadError = null;

// ------------------------------------------------------------------ views / controls (placeholders)

function GridView(){ return html`<p>Grid view (placeholder – filled in Task 3)</p>`; }
function GalleryView(){ return html`<p>Gallery view (placeholder – filled in Task 3)</p>`; }
function RunView(){ return html`<p>Generate view (placeholder – filled in Task 5)</p>`; }
function SetupView(){ return html`<p>Setup view (placeholder – filled in Task 6)</p>`; }
function GridControls(){ return html`<p style="color:var(--muted)">Grid controls (placeholder)</p>`; }
function GalleryControls(){ return html`<p style="color:var(--muted)">Gallery controls (placeholder)</p>`; }

const VIEWS = {grid: GridView, gallery: GalleryView, run: RunView, setup: SetupView};
const CONTROLS = {grid: GridControls, gallery: GalleryControls};

// ------------------------------------------------------------------ app frame

function syncView(){
  const hdr = document.querySelector("header");
  document.documentElement.style.setProperty("--vhdr", (hdr ? hdr.offsetHeight : 0) + "px");
}

function applyHash(){
  const id = hashFocus();
  if(id === focusId()) return;
  state.ui.focus = id;
  if(state.ui.view !== "grid") state.ui.view = "grid";
  update();
}

function onNav(e){
  const b = e.currentTarget;
  state.ui.view = b.dataset.view;
  if(b.dataset.view === "grid" && state.ui.focus){
    state.ui.focus = "";
    history.pushState(null, "", location.pathname + location.search);
  }
  update();
}

function onReload(){
  if(state.isStatic){ location.reload(); return; }
  reloadData().catch(e => { loadError = e.message || String(e); update(); });
}

function App(){
  useLayoutEffect(syncView);

  if(loadError) return html`<p class="empty">Could not load data: ${loadError}</p>`;

  const d = state.data;
  let view = state.ui.view;
  const static$ = state.isStatic;
  if(static$ && (view === "setup" || view === "run")){ view = "grid"; state.ui.view = "grid"; }
  document.documentElement.style.setProperty("--cell", state.ui.size + "px");

  const ok = (d.generations || []).filter(g => g.status === "ok").length;
  const rated = Object.keys(d.ratings || {}).length;
  const status = `${ok} images · ${rated} rated` + (static$ ? " · static copy (read-only)" : "");
  const hidden = static$ ? "display:none" : "";
  const sizeOn = view !== "setup" && view !== "run" ? "on" : "";

  return html`<header>
      <div class="top">
        <h1>ComfyBench</h1>
        <nav>
          <button data-view="grid" class="${view === "grid" ? "on" : ""}" onclick=${onNav}>Compare</button>
          <button data-view="gallery" class="${view === "gallery" ? "on" : ""}" onclick=${onNav}>Gallery</button>
          <button data-view="run" id="nav-run" class="${view === "run" ? "on" : ""}" style="${hidden}" onclick=${onNav}>Generate</button>
          <button data-view="setup" id="nav-setup" class="${view === "setup" ? "on" : ""}" style="${hidden}" onclick=${onNav}>Setup</button>
        </nav>
        <button id="reload" title="Reload" onclick=${onReload}>↻</button>
        <button id="civitai-open" title="Import a picture from Civitai (civitai.com / civitai.red): image into the gallery, prompt into prompts.yaml" style="${hidden}">Civitai import</button>
        <span id="status">${status}</span>
      </div>
      <div class="${view === "grid" ? "ctrl on" : "ctrl"}" id="ctrl-grid">${html`<${CONTROLS.grid}/>`}</div>
      <div class="${view === "gallery" ? "ctrl on" : "ctrl"}" id="ctrl-gallery">${html`<${CONTROLS.gallery}/>`}</div>
      <div class="${sizeOn ? "ctrl on" : "ctrl"}" id="ctrl-size" style="margin-top:6px">
        <label>Size </label><input id="size" type="range" min="160" max="560" value="${state.ui.size}"
          oninput=${e => { state.ui.size = e.target.value; update(); }} aria-label="Size" />
      </div>
    </header>
    <main id="view-grid" class="${view === "grid" ? "on" : ""}">${html`<${VIEWS.grid}/>`}</main>
    <main id="view-gallery" class="${view === "gallery" ? "on" : ""}">${html`<${VIEWS.gallery}/>`}</main>
    <main id="view-run" class="${view === "run" ? "on" : ""}">${html`<${VIEWS.run}/>`}</main>
    <main id="view-setup" class="${view === "setup" ? "on" : ""}">${html`<${VIEWS.setup}/>`}</main>
    <${Toast}/>
  `;
}

async function boot(){
  try {
    if(window.CB_EMBED){
      state.isStatic = true;
      setData(window.CB_EMBED);
    } else {
      await reloadData();
    }
  } catch(e){
    loadError = e.message || String(e);
    render(html`<p class="empty">Could not load data: ${loadError}</p>`, root);
    return;
  }
  state.ui.focus = hashFocus();
  if(state.ui.focus) state.ui.view = "grid";
  if(state.isStatic && (state.ui.view === "setup" || state.ui.view === "run")) state.ui.view = "grid";
  onRender(() => render(html`<${App}/>`, root));
  update();

  window.addEventListener("hashchange", applyHash);
  window.addEventListener("popstate", applyHash);
  window.addEventListener("resize", syncView);
  window.addEventListener("beforeunload", e => {
    if(state.setup.dirty.config || state.setup.dirty.prompts){
      e.preventDefault(); e.returnValue = "";
    }
  });
}

boot();
