import {html, render, useRef} from "cb/vendor/htm-preact.js";

let host = null;
let msg = "";
let timer = null;

export function toast(text){
  msg = text;
  if(host){
    host.style.display = "block";
    host.textContent = text;
  }
  clearTimeout(timer);
  timer = setTimeout(() => { msg = ""; if(host) host.style.display = "none"; }, 1800);
}

export function Toast(){
  const ref = useRef(null);
  host = ref.current;
  return html`<div id="toast" ref=${ref} style="display:${msg ? "block" : "none"}">${msg}</div>`;
}
