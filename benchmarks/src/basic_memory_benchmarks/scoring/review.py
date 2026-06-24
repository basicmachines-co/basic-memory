"""Human-readable judge-review report.

Renders QA judge decisions as a single self-contained HTML file (no external
deps) so a human can skim, sanity-check, and — for judge calibration — label
each verdict agree/disagree/unsure. Labels persist in the browser's
localStorage and export to JSON, feeding the judge-vs-human agreement step.

The point is transparency: every published number rests on the LLM judge, so
the judge's decisions must be auditable at a glance, not buried in JSONL.
"""

from __future__ import annotations

import html
import json

from basic_memory_benchmarks.models import QACaseResult

# --- Page template ---------------------------------------------------------
# Data is injected as a JSON blob; the table is built and filtered client-side.

_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Judge review — {run_id}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 0; padding: 1rem 1.25rem; }}
  h1 {{ font-size: 1.15rem; margin: 0 0 .25rem; }}
  .sub {{ color: #888; margin: 0 0 1rem; }}
  .controls {{ position: sticky; top: 0; background: Canvas; padding: .5rem 0;
    border-bottom: 1px solid #8884; display: flex; gap: .75rem; flex-wrap: wrap;
    align-items: center; z-index: 2; }}
  select, input[type=search] {{ font: inherit; padding: .25rem .4rem; }}
  .counts {{ margin-left: auto; color: #888; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: .5rem; }}
  th, td {{ text-align: left; vertical-align: top; padding: .5rem .6rem;
    border-bottom: 1px solid #8883; }}
  th {{ position: sticky; top: 3.2rem; background: Canvas; font-size: .8rem;
    text-transform: uppercase; letter-spacing: .03em; color: #888; }}
  td.q {{ max-width: 24rem; }}
  td.txt {{ max-width: 28rem; white-space: pre-wrap; }}
  .badge {{ display: inline-block; padding: .1rem .45rem; border-radius: 1rem;
    font-size: .78rem; font-weight: 600; white-space: nowrap; }}
  .ok {{ background: #1a7f3722; color: #1a7f37; }}
  .no {{ background: #c0392b22; color: #c0392b; }}
  .tag {{ color: #888; font-size: .8rem; }}
  .reason {{ color: #aaa; font-size: .85rem; }}
  .labels button {{ font: inherit; margin: 0 .1rem .15rem 0; padding: .15rem .4rem;
    border: 1px solid #8886; border-radius: .35rem; background: transparent; cursor: pointer; }}
  .labels button.sel-agree {{ background: #1a7f37; color: #fff; border-color: #1a7f37; }}
  .labels button.sel-disagree {{ background: #c0392b; color: #fff; border-color: #c0392b; }}
  .labels button.sel-unsure {{ background: #b8860b; color: #fff; border-color: #b8860b; }}
  tr.hidden {{ display: none; }}
</style>
</head>
<body>
<h1>Judge review — {run_id}</h1>
<p class="sub">{n} decisions · judge {judge_model} · labels save in this browser; use Export when done.</p>
<div class="controls">
  <select id="f-provider"><option value="">all providers</option></select>
  <select id="f-category"><option value="">all categories</option></select>
  <select id="f-verdict">
    <option value="">all verdicts</option>
    <option value="correct">correct</option>
    <option value="incorrect">incorrect</option>
    <option value="abstained">abstained</option>
  </select>
  <select id="f-label">
    <option value="">all labels</option>
    <option value="unlabeled">unlabeled</option>
    <option value="agree">agree</option>
    <option value="disagree">disagree</option>
    <option value="unsure">unsure</option>
  </select>
  <input type="search" id="f-text" placeholder="search text…" size="22">
  <button id="export">Export labels (JSON)</button>
  <span class="counts" id="counts"></span>
</div>
<table>
  <thead><tr>
    <th>#</th><th>category</th><th>provider</th><th>question</th>
    <th>gold</th><th>candidate</th><th>verdict</th><th>judge reason</th><th>your label</th>
  </tr></thead>
  <tbody id="rows"></tbody>
</table>
<script id="data" type="application/json">{data_json}</script>
<script>
const RUN = {run_id_json};
const CASES = JSON.parse(document.getElementById("data").textContent);
const KEY = "judge-labels:" + RUN;
const labels = JSON.parse(localStorage.getItem(KEY) || "{{}}");

function uniq(field) {{ return [...new Set(CASES.map(c => c[field]))].sort(); }}
for (const [id, vals] of [["f-provider","provider"],["f-category","category"]]) {{
  const sel = document.getElementById(id);
  for (const v of uniq(vals)) {{ const o = document.createElement("option"); o.value=o.textContent=v; sel.appendChild(o); }}
}}

const tbody = document.getElementById("rows");
CASES.forEach((c, i) => {{
  const tr = document.createElement("tr");
  tr.dataset.provider = c.provider; tr.dataset.category = c.category;
  tr.dataset.verdict = c.error ? "error" : (c.abstained ? "abstained" : (c.correct ? "correct" : "incorrect"));
  tr.dataset.text = (c.question + " " + c.expected_answer + " " + c.generated_answer + " " + c.judge_reason).toLowerCase();
  const verdict = c.error
    ? '<span class="badge no">error</span>'
    : (c.correct ? '<span class="badge ok">correct</span>' : '<span class="badge no">incorrect</span>')
      + (c.abstained ? ' <span class="tag">abstained</span>' : '');
  tr.innerHTML =
    '<td>'+(i+1)+'</td>'
    + '<td class="tag">'+c.category+'</td>'
    + '<td class="tag">'+c.provider+'</td>'
    + '<td class="q">'+c.question+'</td>'
    + '<td class="txt">'+c.expected_answer+'</td>'
    + '<td class="txt">'+(c.generated_answer || '<i class="tag">(empty)</i>')+'</td>'
    + '<td>'+verdict+'</td>'
    + '<td class="reason">'+(c.error ? 'ERROR: '+c.error : c.judge_reason)+'</td>'
    + '<td class="labels" data-id="'+c.query_id+'__'+c.provider+'">'
      + '<button data-l="agree">agree</button>'
      + '<button data-l="disagree">disagree</button>'
      + '<button data-l="unsure">?</button></td>';
  tbody.appendChild(tr);
}});

function renderLabel(cell) {{
  const id = cell.dataset.id; const chosen = labels[id];
  cell.querySelectorAll("button").forEach(b => {{
    b.className = (b.dataset.l === chosen) ? "sel-" + chosen : "";
  }});
}}
document.querySelectorAll("td.labels").forEach(renderLabel);

tbody.addEventListener("click", e => {{
  const b = e.target.closest("button"); if (!b) return;
  const cell = b.closest("td.labels"); const id = cell.dataset.id;
  labels[id] = (labels[id] === b.dataset.l) ? undefined : b.dataset.l;
  if (labels[id] === undefined) delete labels[id];
  localStorage.setItem(KEY, JSON.stringify(labels));
  renderLabel(cell); applyFilters();
}});

const F = id => document.getElementById(id);
function applyFilters() {{
  const fp=F("f-provider").value, fc=F("f-category").value, fv=F("f-verdict").value,
        fl=F("f-label").value, ft=F("f-text").value.trim().toLowerCase();
  let shown = 0;
  tbody.querySelectorAll("tr").forEach(tr => {{
    const id = tr.querySelector("td.labels").dataset.id;
    const lab = labels[id];
    let ok = (!fp||tr.dataset.provider===fp) && (!fc||tr.dataset.category===fc)
      && (!fv||tr.dataset.verdict===fv) && (!ft||tr.dataset.text.includes(ft));
    if (ok && fl) ok = (fl==="unlabeled") ? !lab : (lab===fl);
    tr.classList.toggle("hidden", !ok);
    if (ok) shown++;
  }});
  const labeled = Object.keys(labels).length;
  F("counts").textContent = shown + " shown · " + labeled + "/" + CASES.length + " labeled";
}}
["f-provider","f-category","f-verdict","f-label","f-text"].forEach(id => {{
  F(id).addEventListener("input", applyFilters);
}});
applyFilters();

F("export").addEventListener("click", () => {{
  const out = CASES.map(c => ({{
    query_id: c.query_id, provider: c.provider, category: c.category,
    verdict: c.error ? "error" : (c.abstained ? "abstained" : (c.correct ? "correct" : "incorrect")),
    human_label: labels[c.query_id + "__" + c.provider] || null,
  }})).filter(r => r.human_label);
  const blob = new Blob([JSON.stringify({{run: RUN, labels: out}}, null, 1)], {{type: "application/json"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "judge-labels-" + RUN + ".json"; a.click();
}});
</script>
</body>
</html>
"""


def _case_to_view(case: QACaseResult) -> dict:
    """HTML-escape user-facing text so answers with markup render safely."""
    return {
        "query_id": case.query_id,
        "provider": case.provider,
        "category": case.category,
        "question": html.escape(case.question),
        "expected_answer": html.escape(case.expected_answer),
        "generated_answer": html.escape(case.generated_answer),
        "judge_reason": html.escape(case.judge_reason),
        "correct": case.correct,
        "abstained": case.abstained,
        "error": html.escape(case.error) if case.error else None,
    }


def build_review_html(cases: list[QACaseResult], *, run_id: str) -> str:
    """Render judge decisions as a self-contained review/labeling page."""
    views = [_case_to_view(case) for case in cases]
    judge_model = cases[0].judge_model if cases else "n/a"
    return _HTML_TEMPLATE.format(
        run_id=html.escape(run_id),
        run_id_json=json.dumps(run_id),
        judge_model=html.escape(judge_model),
        n=len(cases),
        data_json=json.dumps(views),
    )
