import {html} from "cb/vendor/htm-preact.js";
import {api} from "cb/api.js";
import {toast} from "cb/components/toast.js";
import {uiDialog, uiPrompt} from "cb/components/dialog.js";
import {saveWorkflow} from "cb/views/setup/logic.js";

// Import a workflow ComfyUI ran recently: GET /history already holds it in API format.
// Resolves with the saved path (workflows/…) or null.
export async function importFromHistory(){
  let d;
  try { d = await api("GET", "api/comfy/history"); } catch(e){ toast(e.message); return null; }
  if(!d.ok){ toast(d.error); return null; }
  if(!d.items.length){ toast("ComfyUI's history has no workflows to import. Run one in ComfyUI first."); return null; }
  const fmt = t => t ? new Date(t).toLocaleString() : "";
  const pick = await uiDialog(() => html`<form method="dialog">
      <h3>Import from ComfyUI history</h3>
      <p class="hint">Workflows ComfyUI ran since its last start, newest first. Re-runs of the same graph are shown once.</p>
      <div class="cb-hist">${d.items.map((it, i) => html`<button value=${String(i)} class="cb-hist-item">
          <b>${it.prompt ? it.prompt.slice(0, 140) : html`<i>no prompt text found</i>`}</b>
          <small>${[it.model, `${it.nodes} nodes`, it.runs > 1 ? `${it.runs} runs` : "", fmt(it.time),
                    it.status === "error" ? "⚠ failed" : "", it.has_output ? "" : "⚠ no SaveImage/PreviewImage"]
                   .filter(Boolean).join(" · ")}</small></button>`)}</div>
      <div class="dlg-btns"><button type="button" data-close>Cancel</button></div>
    </form>`, {wide: true});
  const it = d.items[Number(pick)];
  if(pick === "" || pick === undefined || !it) return null;
  const stem = (it.model.split(/[\\/]/).pop() || "comfyui").replace(/\.[^.]+$/, "")
    .toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "") || "comfyui";
  const name = await uiPrompt("File name in workflows/", {value: `${stem}.json`, ok: "Import"});
  if(!name) return null;
  return saveWorkflow(name, it.workflow);
}
