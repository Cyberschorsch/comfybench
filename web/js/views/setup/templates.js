import {html, useState} from "cb/vendor/htm-preact.js";
import {state} from "cb/store.js";
import {uiDialog, uiConfirm, uiPrompt} from "cb/components/dialog.js";
import {toast} from "cb/components/toast.js";
import {nameRule, RE_ID} from "cb/util.js";
import {markDirty, suErr} from "cb/views/setup/logic.js";
import {Preview} from "cb/views/setup/prompts.js";

// ------------------------------------------------------------------ built-in templates (:862-951, verbatim)
export const BUILTIN_TEMPLATES = {
  "portrait": {
    name: "Portrait", category: "Photorealism",
    prompt: "Close-up portrait photo of a {subject}, {lighting}, {lens} lens, neutral background",
    vars: {
      subject: { woman: "a young woman", man: "a young man", elderly: "an elderly person" },
      lighting: ["soft window light", "golden hour", "studio lighting", "overcast daylight"],
      lens: ["85mm", "50mm", "135mm"]
    },
    vary: "single",
    check: "Facial proportions, eyes, skin texture natural?"
  },
  "character-outfit": {
    name: "Character Outfit", category: "Character",
    prompt: "Full-body shot of a {character} wearing {outfit}, {setting}, {lighting}",
    vars: {
      character: ["young woman", "young man", "elderly woman", "elderly man"],
      outfit: { casual: "casual street wear", formal: "elegant evening wear", armor: "fantasy armor", uniform: "military uniform" },
      setting: ["city street", "forest path", "medieval town", "spaceship corridor"],
      lighting: ["golden hour", "neon lights", "torchlight", "overcast"]
    },
    vary: "product",
    check: "Clothing correct, proportions stable, setting recognizable?"
  },
  "text-render": {
    name: "Text Rendering", category: "Text",
    prompt: 'A {style} sign that reads "{text}", {setting}, {lighting}, photo',
    vars: {
      style: { wooden: "weathered wooden", neon: "neon glowing", metal: "brushed metal", chalk: "chalk on blackboard" },
      text: ["HELLO WORLD", "OPEN", "CAFE", "WELCOME"],
      setting: ["hanging outside a shop", "on a brick wall", "standing on a table"],
      lighting: ["morning light", "night with glow", "golden hour"]
    },
    vary: "zip",
    width: 1024, height: 768,
    check: "Text legible, correct, no hallucination?"
  },
  "img2img-sketch": {
    name: "img2img Sketch → Foto", category: "img2img",
    prompt: "Photograph of a {scene}, {style}, {lighting}, high detail",
    image: "sketch-house.png",
    vars: {
      scene: ["cozy cottage with garden", "modern house with pool", "mountain cabin"],
      style: ["realistic photo", "architectural photography", "cinematic"],
      lighting: ["sunny afternoon", "golden hour", "blue hour", "overcast"],
      denoise: [0.3, 0.5, 0.7, 0.9]
    },
    vary: "single",
    check: "From which denoise on does it follow the sketch? When does it become realistic?"
  },
  "product-photo": {
    name: "Product Photo", category: "Photorealism",
    prompt: "Product photo of a {object} on {surface}, {lighting}, {camera}, commercial photography",
    vars: {
      object: ["perfume bottle", "watch", "smartphone", "cosmetic jar", "sneaker"],
      surface: ["wet black stone", "white marble", "gradient backdrop", "frosted glass"],
      lighting: ["softbox", "rim light", "natural window light", "studio strobes"],
      camera: ["macro lens", "85mm", "100mm macro", "tilt-shift"]
    },
    vary: "single",
    check: "Materials, reflections, sharpness physically plausible?"
  },
  "landscape": {
    name: "Landscape", category: "Landscape",
    prompt: "Wide {view} of {location}, {time}, {weather}, {style}, ultra wide angle",
    vars: {
      view: ["panorama", "aerial view", "drone shot"],
      location: ["mountain lake", "coastal cliffs", "rolling hills", "desert dunes", "forest valley"],
      time: ["sunrise", "golden hour", "blue hour", "midday", "night with stars"],
      weather: ["clear", "misty", "stormy", "foggy", "cloudy"],
      style: ["photorealistic", "cinematic", "painterly"]
    },
    vary: "single",
    width: 1344, height: 768,
    check: "Composition, lighting mood, depth natural?"
  },
  "style-transfer": {
    name: "Style Transfer", category: "Style",
    negative: "",
    prompt: "{subject} in the style of {artist}, {medium}, {details}",
    vars: {
      subject: ["a cat", "a landscape", "a portrait", "a still life", "a cityscape"],
      artist: ["Van Gogh", "Monet", "Picasso", "Hokusai", "Dali", "Klimt"],
      medium: ["oil painting", "watercolor", "woodblock print", "charcoal sketch", "digital art"],
      details: ["vivid colors", "expressive brushstrokes", "flat colors", "surreal elements"]
    },
    vary: "product",
    check: "Style consistently recognizable, not just a filter over a photo?"
  }
};

// ------------------------------------------------------------------ storage / factory (:953-971)
export function loadTemplates(){
  try { return JSON.parse(localStorage.getItem("cb2:prompt-templates") || "{}"); } catch(e){ return {}; }
}
export function saveTemplates(t){ localStorage.setItem("cb2:prompt-templates", JSON.stringify(t)); }
export function allTemplates(){ return {...BUILTIN_TEMPLATES, ...loadTemplates()}; }

export function promptFromTemplate(tmpl, id){
  return {
    id, category: tmpl.category || "",
    prompt: tmpl.prompt || "",
    negative: tmpl.negative !== undefined ? tmpl.negative : undefined,
    vars: tmpl.vars || {},
    vary: tmpl.vary || "product",
    width: tmpl.width, height: tmpl.height,
    check: tmpl.check || "",
    params: tmpl.params || {},
    model_params: tmpl.model_params || {}
  };
}

// ------------------------------------------------------------------ create from template (:1524-1562)
function TemplateModal({close}){
  const su = state.setup;
  const tmpls = allTemplates();
  const builtin = Object.keys(BUILTIN_TEMPLATES);
  const custom = Object.keys(tmpls).filter(k => !builtin.includes(k));
  const cats = [...new Set(su.doc.prompts.map(x => x.category).filter(Boolean))];
  const [key, setKey] = useState(builtin[0]);
  const [pid, setPid] = useState("");
  const [cat, setCat] = useState((tmpls[builtin[0]] || {}).category || "");
  const [err, setErr] = useState("");
  const tmpl = tmpls[key] || {};
  const submit = e => {
    e.preventDefault();
    const id = pid.trim();
    const msg = id ? nameRule(RE_ID, "Letters, digits, - _ .", v => su.doc.prompts.some(x => x.id === v))(id) : "Please enter a prompt ID";
    if(msg){ setErr(msg); return; }
    const newPrompt = promptFromTemplate(tmpl, id);
    newPrompt.category = cat.trim() || tmpl.category || "";
    su.doc.prompts.push(newPrompt); su.prompt = id; markDirty("prompts");
    close("ok");
  };
  return html`<form method="dialog" onSubmit=${submit}>
    <h3>Create prompt from template</h3>
    <p class="hint">Choose a template, enter an ID and create the prompt.</p>
    <div class="su-grid" style="margin-top:12px">
      <label>Template</label>
      <select value=${key} onChange=${e => { const k = e.currentTarget.value; setKey(k); setCat((tmpls[k] || {}).category || ""); }}>
        <optgroup label="Built-in templates">
          ${builtin.map(k => html`<option value=${k}>${tmpls[k].name} (${tmpls[k].category || "–"})</option>`)}
        </optgroup>
        ${custom.length ? html`<optgroup label="Custom templates">
          ${custom.map(k => html`<option value=${k}>${tmpls[k].name} (${tmpls[k].category || "–"})</option>`)}
        </optgroup>` : ""}
      </select>
      <label>Prompt-ID</label>
      <input type="text" placeholder="e.g. my-portrait" autocomplete="off" autofocus value=${pid} onInput=${e => { setPid(e.currentTarget.value); setErr(""); }} />
      <label>Category</label>
      <input type="text" list="su-cats" placeholder="optional" value=${cat} onInput=${e => setCat(e.currentTarget.value)} /><datalist id="su-cats">${cats.map(c => html`<option>${c}</option>`)}</datalist>
    </div>
    <div class="dlg-err">${err}</div>
    <div class="prev"><${Preview} p=${promptFromTemplate(tmpl, pid || "")} /></div>
    <div class="dlg-btns"><button type="button" data-close>Cancel</button><button value="ok" class="btn-p">Create</button></div>
  </form>`;
}

export function showTemplateModal(){
  return uiDialog(close => html`<${TemplateModal} close=${close} />`, {wide: true});
}

// ------------------------------------------------------------------ save current as template (:1564-1581)
export async function saveCurrentAsTemplate(p){
  if(!p){ suErr("No prompt selected"); return; }
  const name = await uiPrompt("Template name", {value: p.id, placeholder: "e.g. My Portraits", ok: "Save template"});
  if(!name){ return; }
  const key = name.toLowerCase().replace(/[^\w]+/g, "-").replace(/^-|-$/g, "");
  const custom = loadTemplates();
  if(custom[key] && !await uiConfirm(`A custom template "${custom[key].name}" already exists.`, {title: "Replace template?", ok: "Replace"})) return;
  custom[key] = {
    name, category: p.category || "",
    prompt: p.prompt, negative: p.negative,
    vars: p.vars || {}, vary: p.vary || "product",
    width: p.width, height: p.height,
    check: p.check || "",
    params: p.params || {}, model_params: p.model_params || {}
  };
  saveTemplates(custom);
  toast(`Template "${name}" saved`);
}

// ------------------------------------------------------------------ template manager (:1583-1607)
function TemplateManager(){
  const [custom, setCustom] = useState(loadTemplates());
  const entries = Object.entries(custom);
  const del = async k => {
    if(!await uiConfirm("It is removed from this browser.", {title: `Delete template "${custom[k].name}"?`, ok: "Delete", danger: true})) return;
    const c = {...loadTemplates()}; delete c[k]; saveTemplates(c); setCustom(c); toast("Template deleted");
  };
  return html`<form method="dialog">
    <h3>Manage templates</h3>
    <p class="hint">Built-in templates cannot be deleted. Custom templates are stored in the browser.</p>
    <div style="margin-top:8px">${!entries.length ? html`<p class="hint">No custom templates yet.</p>` : html`<table class="kv">
      ${entries.map(([k, t]) => html`<tr key=${k}>
        <td class="k">${t.name} <span class="hint">(${k})</span></td>
        <td>${t.category || "–"}</td>
        <td style="width:40px"><button type="button" class="x" title="Delete" onClick=${() => del(k)}>✕</button></td>
      </tr>`)}</table>`}</div>
    <div class="dlg-btns"><button value="" autofocus>Close</button></div>
  </form>`;
}

export function showTemplateManager(){
  return uiDialog(() => html`<${TemplateManager} />`, {wide: true});
}
