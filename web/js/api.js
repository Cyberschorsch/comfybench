import {toast} from "cb/components/toast.js";
import {state} from "cb/store.js";

export async function api(method, url, body){
  const r = await fetch(url, {
    method,
    headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : undefined
  });
  let j = {};
  try { j = await r.json(); } catch(e){}
  if(!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}

export function fileUrl(rel){
  return (state.isStatic ? "" : "files/") + rel.split("/").map(encodeURIComponent).join("/");
}

export function pickFile(accept){
  return new Promise(res => {
    const i = document.createElement("input");
    i.type = "file"; i.accept = accept;
    i.onchange = () => res(i.files[0] || null);
    i.click();
  });
}

export async function readDataUrl(file){
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

export async function copy(text){
  try { await navigator.clipboard.writeText(text); }
  catch(e){
    const a = document.createElement("textarea");
    a.value = text; document.body.append(a); a.select();
    document.execCommand("copy"); a.remove();
  }
  toast("Copied");
}
