import {html} from "cb/vendor/htm-preact.js";
import {update} from "cb/store.js";

let msg = "";
let timer = null;

export function toast(text){
  msg = text;
  update();
  clearTimeout(timer);
  timer = setTimeout(() => { msg = ""; update(); }, 1800);
}

export function Toast(){
  return html`<div id="toast" style=${"display:" + (msg ? "block" : "none")}>${msg}</div>`;
}
