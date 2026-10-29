import {html, useState, useRef} from "cb/vendor/htm-preact.js";
import {state, update, reloadData} from "cb/store.js";
import {api} from "cb/api.js";
import {uiDialog} from "cb/components/dialog.js";
import {toast} from "cb/components/toast.js";
import {RE_ID} from "cb/util.js";

// ------------------------------------------------------------------ Civitai import (:1074-1145, verbatim)
// One modal: paste a URL → preview loads in place → adjust prompt ID / category → Import.

function categories(){
  return [...new Set((state.data.prompts || []).map(x => x.category).filter(Boolean))];
}

function idMsg(v){
  return !v ? "" : !RE_ID.test(v) ? "Letters, digits, - _ . only"
    : (state.data.prompts || []).some(x => x.id === v) ? `"${v}" exists – the entry will be skipped, the image is still imported` : "";
}

function CivitaiDialog({close}){
  const [url, setUrl] = useState("");
  const [pv, setPv] = useState(null);
  const [pvUrl, setPvUrl] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [pid, setPid] = useState("");
  const [cat, setCat] = useState("Civitai");
  const urlRef = useRef(null), pidRef = useRef(null), formRef = useRef(null);

  const reset = () => { setPv(null); setPvUrl(""); };

  const submit = async e => {
    e.preventDefault();                     // stays open until the import really finished
    if(busy || importing) return;
    const u = url.trim();
    if(!pv){
      if(!u){ setErr("Paste a Civitai picture URL"); if(urlRef.current) urlRef.current.focus(); return; }
      setBusy(true);
      try {
        const p = await api("POST", "api/civitai/preview", {url: u});   // fetches metadata only – writes nothing
        setPv(p); setPvUrl(u); setPid(p.prompt_id); setCat("Civitai");
        setErr(idMsg(String(p.prompt_id || "").trim()));
      } catch(ex){ reset(); setErr(ex.message); if(urlRef.current) urlRef.current.focus(); }
      setBusy(false);
      return;
    }
    const id = pid.trim();
    if(!id){ setErr("Please enter a prompt ID"); if(pidRef.current) pidRef.current.focus(); return; }
    if(!RE_ID.test(id)){ setErr("Letters, digits, - _ . only"); if(pidRef.current) pidRef.current.focus(); return; }
    setImporting(true);
    try {
      const r = await api("POST", "api/import-url", {url: pvUrl, prompt_id: id, category: cat.trim()});
      close("ok");
      toast((r.image_added ? "Image imported" : "Image already there") + " · prompt " + r.prompt_id + (r.prompt_added ? " added" : " already exists"));
      await reloadData();
      state.ui.view = "gallery"; update();   // show where the picture landed
    } catch(ex){ setErr(ex.message); setImporting(false); }
  };

  const facts = pv ? [pv.params && pv.params.steps != null ? pv.params.steps + " steps" : null,
    pv.params && pv.params.cfg != null ? "cfg " + pv.params.cfg : null,
    pv.params && pv.params.sampler, pv.params && pv.params.scheduler,
    pv.width && pv.height ? pv.width + "×" + pv.height : null].filter(Boolean).join(" · ") : "";

  return html`<form method="dialog" ref=${el => formRef.current = el} onSubmit=${submit}>
    <h3>Import from Civitai</h3>
    <label class="dlg-l">Picture URL<input type="text" id="civ-url" ref=${el => urlRef.current = el}
      placeholder="https://civitai.com/images/…  or  civitai.red/images/…" autocomplete="off" spellcheck="false" autofocus
      value=${url} onInput=${e => { const v = e.currentTarget.value; setUrl(v); setErr(""); if(pv && v.trim() !== pvUrl) reset(); }}
      onPaste=${() => setTimeout(() => { if(urlRef.current && urlRef.current.value.trim()) formRef.current.requestSubmit(); })} /></label>
    <div id="civ-pv">${pv ? html`
      <div style="display:flex;gap:12px;margin-top:12px;align-items:flex-start">
        ${pv.thumb ? html`<img src=${pv.thumb} alt="Civitai picture" style="width:150px;max-height:230px;object-fit:contain;background:var(--bg);border:1px solid var(--line);border-radius:8px" />` : ""}
        <div style="flex:1;min-width:0">
          <div class="hint"><a href=${pv.url} target="_blank" rel="noopener">${pv.url}</a></div>
          <div style="margin-top:6px"><b>${pv.model || "model not shared"}</b></div>
          <div class="hint">${facts}</div>
        </div>
      </div>
      <div class="su-grid" style="margin-top:12px">
        <label for="civ-pid">Prompt-ID</label>
        <input type="text" id="civ-pid" ref=${el => pidRef.current = el} autocomplete="off" spellcheck="false"
          value=${pid} onInput=${e => { const v = e.currentTarget.value; setPid(v); setErr(idMsg(v.trim())); }} />
        <label for="civ-cat">Category</label>
        <input type="text" id="civ-cat" list="civ-cats" autocomplete="off"
          value=${cat} onInput=${e => setCat(e.currentTarget.value)} />
        <datalist id="civ-cats">${categories().map(c => html`<option>${c}</option>`)}</datalist>
      </div>
      <label class="dlg-l" style="margin-top:10px">Prompt<textarea readonly=${true} rows="4">${pv.prompt}</textarea></label>
      ${pv.negative ? html`<label class="dlg-l" style="margin-top:8px">Negative<textarea readonly=${true} rows="2">${pv.negative}</textarea></label>` : ""}
    ` : ""}</div>
    <div class="dlg-err">${err}</div>
    <div class="dlg-btns"><button type="button" data-close>Cancel</button><button class="btn-p" disabled=${busy || importing}>${busy ? "Loading…" : pv ? "Import" : "Preview"}</button></div>
  </form>`;
}

export function openCivitaiDialog(){
  return uiDialog(close => html`<${CivitaiDialog} close=${close} />`, {wide: true});
}
