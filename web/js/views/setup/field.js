import {html, useRef} from "cb/vendor/htm-preact.js";
import {parseScalar, showScalar, getAt, setAt, nameRule, RE_NAME} from "cb/util.js";
import {uiPrompt} from "cb/components/dialog.js";
import {OptSelect} from "cb/components/common.js";
import {rootOf, suWhich, markDirty} from "cb/views/setup/logic.js";

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// Value of a bound field, parsed from the raw text (old readBound :1613-1619).
export function readValue(type, raw){
  if(type === "scalar") return parseScalar(raw);
  if(type === "seeds"){
    const xs = String(raw).split(/[,\s]+/).filter(Boolean).map(x => parseInt(x, 10)).filter(x => !isNaN(x));
    return xs.length ? xs : undefined;
  }
  if(type === "keep") return raw;
  return raw === "" ? undefined : raw;
}
// Formatted display value (old fieldVal :1005-1009).
function displayVal(v, type){
  if(type === "seeds") return Array.isArray(v) ? v.join(", ") : "";
  return showScalar(v);
}

// <Field root="cfg"|"doc" path=[…] type="text"|"scalar"|"seeds"|"keep" as="input"|"textarea" …rest/>
// Keeps the user's raw text as long as it still parses to the model value, so typing
// "0.50" or "42, " is not rewritten under the cursor. External changes still show up
// because the raw text is dropped once the model value no longer matches it.
export function Field({root, path, type = "text", as = "input", ...rest}){
  const model = getAt(rootOf(root), path);
  const raw = useRef(null);
  const shown = raw.current !== null && same(readValue(type, raw.current), model)
    ? raw.current : displayVal(model, type);
  const onInput = e => {
    raw.current = e.currentTarget.value;
    setAt(rootOf(root), path, readValue(type, raw.current));
    markDirty(suWhich(root));
  };
  if(as === "textarea")
    return html`<textarea ...${rest} onInput=${onInput}>${shown}</textarea>`;
  return html`<input type="text" ...${rest} value=${shown} onInput=${onInput} />`;
}

// ------------------------------------------------------------------ Key/value table (:1278-1289)
async function kvAdd(root, path){
  const k = await uiPrompt("Parameter name", {placeholder: "e.g. steps, cfg, sampler", ok: "Add",
    validate: nameRule(RE_NAME, "Letters, digits and _")});
  if(!k) return;
  if(getAt(rootOf(root), [...path, k]) === undefined) setAt(rootOf(root), [...path, k], "");
  markDirty(suWhich(root)); return;
}
function kvDel(root, path){
  setAt(rootOf(root), path, undefined);
  const parent = getAt(rootOf(root), path.slice(0, -1));
  if(parent && !Object.keys(parent).length) setAt(rootOf(root), path.slice(0, -1), undefined);
  markDirty(suWhich(root));
}

// Decide input vs. textarea once per row: switching element type mid-typing would blur.
function KvValue({root, path, v}){
  const long = useRef(typeof v === "string" && (v.length > 60 || v.includes("\n"))).current;
  return long ? html`<${Field} root=${root} path=${path} type="scalar" as="textarea" rows="2" />`
              : html`<${Field} root=${root} path=${path} type="scalar" />`;
}

export function KvTable({root, path, obj, optsOf}){
  const rows = Object.entries(obj).map(([k, v]) => {
    const opts = optsOf && optsOf(k);
    const input = opts
      ? html`<${OptSelect} value=${v} options=${opts} onChange=${val => {
          setAt(rootOf(root), [...path, k], readValue("scalar", val));
          markDirty(suWhich(root));
        }} />`
      : html`<${KvValue} root=${root} path=${[...path, k]} v=${v} />`;
    return html`<tr key=${k}><td class="k">${k}</td><td>${input}</td>
      <td style="width:30px"><button class="x" onClick=${() => kvDel(root, [...path, k])}>✕</button></td></tr>`;
  });
  return html`<table class="kv">${rows}</table>
    <button style="margin-top:6px" onClick=${() => kvAdd(root, path)}>＋ Value</button>
    <span class="hint">Numbers and true/false are saved as such automatically.</span>`;
}
