import {html, useState} from "cb/vendor/htm-preact.js";
import {state, update} from "cb/store.js";
import {api} from "cb/api.js";
import {toast} from "cb/components/toast.js";
import {Field} from "cb/views/setup/field.js";
import {markDirty, suErr} from "cb/views/setup/logic.js";

// Switching the judge off removes the section from config.yaml; the values are kept here so switching it
// back on in the same session restores them.
function toggle(on){
  const su = state.setup;
  if(on) su.cfg.judge = su.judgeStash || {url: "", model: ""};
  else { su.judgeStash = su.cfg.judge; delete su.cfg.judge; }
  markDirty("config");
}

// The key lives outside config.yaml (env or <data>/.judge-key) and is saved right away, not with "Save".
function KeyRow(){
  const su = state.setup;
  const [key, setKey] = useState("");
  const src = su.data.judge_key || "none";
  const save = async k => {
    try {
      su.data.judge_key = (await api("PUT", "api/setup/judge-key", {key: k})).judge_key;
      setKey("");
      toast(k ? "Key saved" : "Key removed");
      update();
    } catch(e){ suErr(e.message); }
  };
  if(src === "env") return html`<div class="hint" id="jKeyState">Set via the environment variable <code>COMFYBENCH_JUDGE_KEY</code> – change it there.</div>`;
  return html`<div>
    <div class="row">
      <input type="password" id="jKey" autocomplete="off" placeholder=${src === "file" ? "•••••••• (saved) – enter a new key to replace it" : "only if the endpoint needs one"}
        value=${key} onInput=${e => setKey(e.currentTarget.value)} />
      <button disabled=${!key.trim()} onClick=${() => save(key)}>Save key</button>
      ${src === "file" ? html`<button onClick=${() => save("")}>Remove</button>` : ""}
    </div>
    <div class="hint" id="jKeyState">${src === "file" ? "Key saved in the data folder (.judge-key), not in config.yaml." : "No key set."} The environment variable COMFYBENCH_JUDGE_KEY takes precedence.</div>
  </div>`;
}

function TestRow(){
  const j = state.setup.cfg.judge || {};
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true); setRes(null);
    try { setRes(await api("POST", "api/judge/test", {url: j.url, model: j.model})); }
    catch(e){ setRes({ok: false, error: e.message}); }
    setBusy(false);
  };
  return html`<div>
    <button id="jTest" disabled=${busy || !(j.url || "").trim() || !(j.model || "").trim()} onClick=${run}>${busy ? "Testing …" : "Test connection"}</button>
    ${res ? html`<div class="su-msg ${res.ok ? "ok" : "err"}" id="jTestRes" style="margin-top:6px">${res.ok
      ? `✓ Answered in ${res.seconds} s${res.reply ? ": " + res.reply : ""}`
      : "✗ " + res.error}</div>` : ""}
    <div class="hint">Uses the values above, even unsaved. Checks address, key and model name – not whether the model can see images.</div>
  </div>`;
}

export function Judge(){
  const su = state.setup;
  const on = !!su.cfg.judge;
  return html`<div class="su-edit"><h2>Judge</h2>
    <p class="hint" style="margin-top:0">A vision LLM answers yes/no questions per image (see README, "Judge"). Any OpenAI-compatible endpoint works: Ollama, LM Studio, vLLM, llama.cpp or a cloud API.</p>
    <label class="row" style="gap:8px;margin-bottom:12px"><input type="checkbox" id="jOn" checked=${on} onChange=${e => toggle(e.currentTarget.checked)} /> Use judge</label>
    ${on ? html`<div class="su-grid">
      <label>Endpoint URL</label><${Field} root="cfg" path=${["judge", "url"]} id="jUrl" placeholder="http://127.0.0.1:11434/v1" />
      <label>Model</label><div><${Field} root="cfg" path=${["judge", "model"]} id="jModel" placeholder="qwen2.5vl:7b" /><div class="hint">Changing the model marks existing results as stale.</div></div>
      <label>API key</label><${KeyRow} />
      <label></label><${TestRow} />
      <label style="align-self:start">Quality questions</label><div><${Field} root="cfg" path=${["judge", "quality"]} type="lines" as="textarea" rows="4" id="jQuality"
        placeholder="Are hands, faces and limbs anatomically correct (if present)?" /><div class="hint">Optional, asked for every image. One question per line; a "yes" must mean good. Changes mark existing results as stale.</div></div>
    </div>
    <p class="hint">The fidelity questions belong to each prompt – edit them under Prompts.</p>` : ""}
  </div>`;
}
