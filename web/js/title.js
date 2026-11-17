// Window/tab title with the progress of the active run.
// Free of imports so node --test can load it (web/tests/title.test.js).

export const BASE_TITLE = "ComfyBench";

const ACTIVE_STATES = ["running", "stopping"];

// Same counters the nav button and the status panel show: everything that
// leaves the queue, whether it produced an image, was skipped or failed.
export function finishedCount(st){
  return (st.done || 0) + (st.skipped || 0) + (st.failed || 0) + (st.idle || 0);
}

// "Judging 3/10 · ComfyBench" while a judge runs, "Generating 3/10 · ComfyBench"
// while images are generated, plain "ComfyBench" otherwise.
export function runTitle(st){
  if(!st || !ACTIVE_STATES.includes(st.state)) return BASE_TITLE;
  const verb = st.kind === "judge" ? "Judging" : "Generating";
  return `${verb} ${finishedCount(st)}/${st.total || 0} · ${BASE_TITLE}`;
}

export function applyTitle(st, doc = typeof document === "undefined" ? null : document){
  if(!doc) return;
  const next = runTitle(st);
  if(doc.title !== next) doc.title = next;
}
