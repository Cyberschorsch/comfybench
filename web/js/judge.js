// Judge math, free of imports so node --test can load it (web/tests/judge.test.js).

const yes = pairs => pairs.filter(([, a]) => a === "yes").length;

// {text: "3/4 · 3/3", stale, title} for one judge.json entry, or null.
export function badge(e){
  if(!e) return null;
  const fid = e.fidelity || [], qual = e.quality || [];
  const parts = [`${yes(fid)}/${fid.length}`];
  if(qual.length) parts.push(`${yes(qual)}/${qual.length}`);
  const no = [...fid, ...qual].filter(([, a]) => a !== "yes").map(([q]) => "✗ " + q);
  const title = [`Judge: fidelity ${parts[0]}` + (qual.length ? ` · quality ${parts[1]}` : ""), ...no].join("\n");
  return {text: parts.join(" · "), stale: !!e.stale, title};
}

// Mean scores over current entries; stale or missing entries and null scores are left out.
export function aggregate(entries){
  const cur = entries.filter(e => e && !e.stale);
  const mean = key => {
    const xs = cur.map(e => e[key]).filter(x => typeof x === "number");
    return {mean: xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null, n: xs.length};
  };
  return {fidelity: mean("fidelity_score"), quality: mean("quality_score")};
}

function ranks(xs){
  const order = xs.map((x, i) => [x, i]).sort((a, b) => a[0] - b[0]);
  const r = new Array(xs.length);
  for(let i = 0; i < order.length;){
    let j = i;
    while(j + 1 < order.length && order[j + 1][0] === order[i][0]) j++;
    for(let k = i; k <= j; k++) r[order[k][1]] = (i + j) / 2 + 1;   // ties share the average rank
    i = j + 1;
  }
  return r;
}

// Spearman rank correlation, or null below 10 pairs or without variance on one side.
export function spearman(xs, ys){
  if(xs.length !== ys.length || xs.length < 10) return null;
  const a = ranks(xs), b = ranks(ys);
  const m = a.reduce((s, x) => s + x, 0) / a.length;   // both rank lists have the same mean
  let num = 0, da = 0, db = 0;
  a.forEach((x, i) => { num += (x - m) * (b[i] - m); da += (x - m) ** 2; db += (b[i] - m) ** 2; });
  return da && db ? num / Math.sqrt(da * db) : null;
}

// Sort comparator: lowest current fidelity first, then stale entries, then unjudged.
export function worstFirst(a, b){
  const key = e => !e || typeof e.fidelity_score !== "number" ? [2, 0] : e.stale ? [1, 0] : [0, e.fidelity_score];
  const [ka, kb] = [key(a), key(b)];
  return ka[0] - kb[0] || ka[1] - kb[1];
}
