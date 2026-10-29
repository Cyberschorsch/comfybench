import {html, render} from "cb/vendor/htm-preact.js";

// Create a <dialog class="dlg [wide]>, render Preact content into it, showModal().
// Backdrop click (mousedown+click on the dialog itself) or Esc closes; resolving
// with the value passed to close(value) (undefined for backdrop/Esc/cancel).
export async function uiDialog(renderFn, {wide = false} = {}){
  const d = document.createElement("dialog");
  d.className = "dlg" + (wide ? " wide" : "");
  let downOnBackdrop = false;
  d.addEventListener("mousedown", e => { downOnBackdrop = e.target === d; });
  d.addEventListener("click", e => {
    if((e.target === d && downOnBackdrop) || e.target.closest("[data-close]")) d.close();
  });
  return new Promise(res => {
    d.addEventListener("close", () => { render(null, d); d.remove(); res(d.returnValue); }, {once: true});
    document.body.append(d);
    render(renderFn((value) => d.close(value)), d);
    d.showModal();
  });
}
export function uiConfirm(msg, {title = "", ok = "OK", danger = false} = {}){
  // Confirm: texts/classes/markup like report_template.html:279-285.
  return uiDialog(close => html`
    <form method="dialog">
      ${title ? html`<h3>${title}</h3>` : ""}
      <p>${msg}</p>
      <div class="dlg-btns">
        <button type="button" data-close>Cancel</button>
        <button value="ok" class="${danger ? "btn-d" : "btn-p"}" autofocus>${ok}</button>
      </div>
    </form>
  `).then(ret => ret === "ok");
}

// Prompt: resolves with the trimmed value, or null when cancelled.
// validate(v) returns an error message or "" (like report_template.html:287-299).
export function uiPrompt(label, {value = "", placeholder = "", ok = "OK", validate} = {}){
  let inputEl = null, errEl = null;
  const p = uiDialog(close => html`
    <form method="dialog" onSubmit=${e => {
      const v = inputEl.value.trim();
      const msg = v ? (validate ? validate(v) : "") || "" : "Please enter a value";
      if(msg){ e.preventDefault(); errEl.textContent = msg; inputEl.focus(); }
    }}>
      <label class="dlg-l">${label}<input ref=${el => inputEl = el} type="text" value="${value}"
        placeholder="${placeholder}" autocomplete="off" spellcheck="false"
        onInput=${() => { errEl.textContent = ""; }} /></label>
      <div ref=${el => errEl = el} class="dlg-err"></div>
      <div class="dlg-btns">
        <button type="button" data-close>Cancel</button>
        <button value="ok" class="btn-p" autofocus>${ok}</button>
      </div>
    </form>
  `);
  // Select the input text when the dialog opens (like report_template.html:292).
  if(inputEl) inputEl.select();
  return p.then(ret => ret === "ok" ? inputEl.value.trim() : null);
}
