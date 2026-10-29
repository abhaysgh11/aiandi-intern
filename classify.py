#!/usr/bin/env python3
"""Classify forum comments as acceptable / toxic / spam with Cloudflare's Clef.

Usage:
    python classify.py          # runs both: v1 (clef-flash) and v2 (clef)
    python classify.py v1       # comments.json    -> results.html     (clef-flash)
    python classify.py v2       # comments-v2.json -> results-v2.html  (clef)

Credentials are read from cloud-flare.local.sh (never put them in this file).
Python standard library only.
"""
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

CREDS_FILE = "cloud-flare.local.sh"
LOW_CONFIDENCE = 0.5   # answers below this are flagged "check me" in the HTML
WORKERS = 4            # parallel requests; lower this if you hit HTTP 429 a lot

CONFIGS = {
    "v1": {"model": "clef-flash", "input": "comments.json", "output": "results.html"},
    "v2": {"model": "clef", "input": "comments-v2.json", "output": "results-v2.html"},
}

# ---------------------------------------------------------------------------
# Classification criteria. The quality of these descriptions decides how many
# mistakes Clef makes. For step 7, edit CRITERIA_V2 after reading your
# MISTAKES.md: add rules that fix the kinds of errors you found.
# ---------------------------------------------------------------------------
INSTRUCTIONS = (
    "Classify `comment`, a post written by a student in the discussion forum of "
    "an online college course on Generative AI (ChatGPT, Claude, Gemini, prompts, "
    "AI tools, weekly exercises, quizzes, certificates, live sessions). Decide "
    "whether the comment is acceptable, toxic or spam."
)

CRITERIA_V1 = {
    "acceptable": (
        "A genuine student comment that belongs on a course forum: a question, an "
        "answer, help for another student, feedback, a complaint or frustration "
        "about the course/platform/tools, disagreement, or a blunt or critical "
        "opinion expressed without attacking a person. Strong criticism of the "
        "course, the instructors or an AI tool is acceptable. Mild or casual "
        "language is fine. Sharing a relevant link or resource is acceptable."
    ),
    "toxic": (
        "Abusive or hostile content aimed at people: insults, name-calling, "
        "personal attacks, mocking or humiliating someone, harassment, threats, "
        "hate or slurs about religion, caste, gender, region, language, etc., or "
        "telling someone to quit or that they are stupid. Being harsh about an "
        "idea or a product is NOT toxic; attacking a person is."
    ),
    "spam": (
        "Unwanted promotional or irrelevant content: ads, selling courses, "
        "services, followers, or products, referral or discount links, job or "
        "money-making schemes, crypto/betting/loan offers, repeated copy-pasted "
        "text, chain messages, gibberish, or links with no connection to the "
        "course. Also pushing the poster's own unrelated channel or business."
    ),
}

# Starting point for v2: same as v1 plus extra tie-break rules for harder cases.
# Replace/extend these with what YOU learn from reviewing MISTAKES.md.

# Starting point for v2: v1 criteria plus lessons from reviewing MISTAKES.md
# Starting point for v2: v1 criteria plus lessons from reviewing MISTAKES.md
CRITERIA_V2 = dict(CRITERIA_V1)

CRITERIA_V2["acceptable"] += (
    " Criticism of a course, teacher, TA, grading decision, or project is "
    "acceptable when it focuses on the issue rather than attacking a person. "
    "Sarcasm or a rude tone alone does not make a comment toxic. "
    "Do not label a comment toxic merely because it disagrees with someone."
)

CRITERIA_V2["toxic"] += (
    " Personal insults, including sarcastic or indirect insults, are toxic "
    "even without swear words. This includes calling someone clueless or "
    "suggesting they are unfit for their role through personal attacks. "
    "Comments that stereotype a group, such as claiming girls receive good "
    "marks only because a teacher favors them, are toxic. "
    "Broad accusations that an entire group cheats or copies, especially "
    "claims that this behavior is in their blood, are toxic. "
    "Distinguish these attacks from specific, evidence-based concerns about "
    "grading or plagiarism."
)

CRITERIA_V2["spam"] += (
    " A comment that is on-topic in its first lines but mainly exists to push a "
    "link, product or service is spam. A relevant link shared to help someone is "
    "not spam."

)
CRITERIA = {"v1": CRITERIA_V1, "v2": CRITERIA_V2}
LABELS = ["acceptable", "toxic", "spam"]


# ---------------------------------------------------------------------------
def load_credentials():
    try:
        text = open(CREDS_FILE).read()
    except FileNotFoundError:
        sys.exit(f"Missing {CREDS_FILE}. Copy cloud-flare.sh to {CREDS_FILE} and fill it in.")
    creds = dict(re.findall(r'export (\w+)="([^"]*)"', text))
    try:
        return creds["CLOUDFLARE_ACCOUNT_ID"], creds["CLOUDFLARE_API_TOKEN"]
    except KeyError as e:
        sys.exit(f"{e} not found in {CREDS_FILE} (expected lines like export NAME=\"value\").")


def load_comments(path):
    """Return a list of {'id': ..., 'text': ..., 'extra': {...}} from the JSON file."""
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict):  # e.g. {"comments": [...]}
        lists = [v for v in data.values() if isinstance(v, list)]
        if not lists:
            sys.exit(f"{path}: expected a list of comments.")
        data = lists[0]
    comments = []
    for i, item in enumerate(data, 1):
        if isinstance(item, str):
            comments.append({"id": i, "text": item, "extra": {}})
            continue
        text_key = next((k for k in ("text", "comment", "body", "content", "message")
                         if isinstance(item.get(k), str)), None)
        if text_key is None:  # fall back to the longest string field
            strings = {k: v for k, v in item.items() if isinstance(v, str)}
            if not strings:
                sys.exit(f"{path}: can't find the comment text in {item}")
            text_key = max(strings, key=lambda k: len(strings[k]))
        cid = item.get("id", item.get("comment_id", i))
        extra = {k: v for k, v in item.items() if k not in (text_key, "id", "comment_id")}
        comments.append({"id": cid, "text": item[text_key], "extra": extra})
    return comments


def classify(comment, version, model, account, token):
    payload = {
        "state": {"comment": comment["text"]},
        "questions": {
            "label": {
                "type": "choice",
                "instructions": INSTRUCTIONS,
                "criteria": CRITERIA[version],
            }
        },
    }
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/cloudflare/{model}"
    last_error = None
    for attempt in range(6):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            if not data.get("success"):
                raise RuntimeError(f"API said success=false: {data.get('errors')}")
            ans = data["result"]["answers"]["label"]
            return {"label": ans["choice"], "confidence": ans["confidence"],
                    "probs": ans["probabilities"]}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit("HTTP %d: bad Cloudflare account ID or API token "
                         "(needs Workers AI - Read and Edit)." % e.code)
            last_error = f"HTTP {e.code}"
            time.sleep(2 ** attempt)  # 429 / 5xx: wait and retry
        except (urllib.error.URLError, TimeoutError, RuntimeError) as e:
            last_error = str(e)
            time.sleep(2 ** attempt)
    return {"label": "error", "confidence": 0.0, "probs": {}, "error": last_error}


# ---------------------------------------------------------------------------
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --bg:#f6f7f9; --card:#fff; --text:#1c2230; --muted:#667085; --line:#e4e7ec;
           --ok:#12805c; --okbg:#e3f6ee; --tox:#b42318; --toxbg:#fde8e6;
           --spam:#a15c07; --spambg:#fdf0d5; --err:#555; --errbg:#eee; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#10131a; --card:#181c26; --text:#e8ebf2; --muted:#9aa3b5; --line:#2a3040;
             --ok:#5fd4a6; --okbg:#14372b; --tox:#ff9b91; --toxbg:#431a17;
             --spam:#f3c261; --spambg:#3d2e0a; --err:#bbb; --errbg:#2a2a2a; }} }}
  body {{ margin:0; font:15px/1.5 system-ui,sans-serif; background:var(--bg); color:var(--text); }}
  header {{ position:sticky; top:0; background:var(--bg); padding:14px 20px;
            border-bottom:1px solid var(--line); z-index:5; }}
  h1 {{ margin:0 0 4px; font-size:18px; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:10px; }}
  .controls {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
  button, select, input[type=search] {{ font:inherit; padding:5px 10px; border-radius:8px;
            border:1px solid var(--line); background:var(--card); color:var(--text); }}
  button.on {{ outline:2px solid var(--text); }}
  main {{ max-width:900px; margin:0 auto; padding:16px 20px 60px; }}
  .row {{ background:var(--card); border:1px solid var(--line); border-left-width:6px;
          border-radius:10px; padding:10px 14px; margin-bottom:10px; }}
  .row.acceptable {{ border-left-color:var(--ok); }}
  .row.toxic {{ border-left-color:var(--tox); }}
  .row.spam {{ border-left-color:var(--spam); }}
  .row.error {{ border-left-color:var(--err); }}
  .top {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:4px; }}
  .id {{ color:var(--muted); font-size:13px; font-variant-numeric:tabular-nums; }}
  .tag {{ font-size:12px; font-weight:600; padding:2px 9px; border-radius:99px; }}
  .tag.acceptable {{ background:var(--okbg); color:var(--ok); }}
  .tag.toxic {{ background:var(--toxbg); color:var(--tox); }}
  .tag.spam {{ background:var(--spambg); color:var(--spam); }}
  .tag.error {{ background:var(--errbg); color:var(--err); }}
  .conf {{ font-size:12px; color:var(--muted); }}
  .low {{ font-size:12px; font-weight:600; color:var(--spam); }}
  .text {{ white-space:pre-wrap; overflow-wrap:anywhere; }}
  .meta {{ color:var(--muted); font-size:12px; margin-top:4px; }}
  .hidden {{ display:none; }}
</style></head><body>
<header>
  <h1>{title}</h1>
  <div class="sub">Model: {model} &middot; {total} comments &middot; {counts} &middot;
    <span id="shown"></span></div>
  <div class="controls">
    <button class="f on" data-l="all">All</button>
    <button class="f" data-l="acceptable">Acceptable</button>
    <button class="f" data-l="toxic">Toxic</button>
    <button class="f" data-l="spam">Spam</button>
    <label><input type="checkbox" id="low"> low confidence only (&lt;{thr})</label>
    <select id="sort">
      <option value="id">Sort: original order</option>
      <option value="conf">Sort: lowest confidence first</option>
    </select>
    <input type="search" id="q" placeholder="Search text or id">
  </div>
</header>
<main id="list">
{rows}
</main>
<script>
  const rows = [...document.querySelectorAll('.row')];
  const list = document.getElementById('list');
  let label = 'all';
  function apply() {{
    const low = document.getElementById('low').checked;
    const q = document.getElementById('q').value.toLowerCase();
    const sort = document.getElementById('sort').value;
    const ordered = [...rows].sort((a, b) =>
      sort === 'conf' ? a.dataset.conf - b.dataset.conf : a.dataset.idx - b.dataset.idx);
    let n = 0;
    ordered.forEach(r => {{
      const show = (label === 'all' || r.dataset.label === label)
        && (!low || r.dataset.low === '1')
        && (!q || r.textContent.toLowerCase().includes(q));
      r.classList.toggle('hidden', !show);
      if (show) n++;
      list.appendChild(r);
    }});
    document.getElementById('shown').textContent = 'showing ' + n;
  }}
  document.querySelectorAll('.f').forEach(b => b.onclick = () => {{
    document.querySelectorAll('.f').forEach(x => x.classList.remove('on'));
    b.classList.add('on'); label = b.dataset.l; apply();
  }});
  ['low', 'sort', 'q'].forEach(id => document.getElementById(id).addEventListener('input', apply));
  apply();
</script></body></html>
"""


def render_html(version, model, comments, results, out_path):
    e = html.escape
    counts = {}
    rows = []
    for idx, (c, r) in enumerate(zip(comments, results)):
        label = r["label"]
        counts[label] = counts.get(label, 0) + 1
        low = label == "error" or r["confidence"] < LOW_CONFIDENCE
        probs = " &middot; ".join(f"{e(k)} {v:.0%}" for k, v in
                                  sorted(r["probs"].items(), key=lambda kv: -kv[1]))
        extra = ", ".join(f"{e(str(k))}: {e(str(v))}" for k, v in c["extra"].items())
        meta = " | ".join(x for x in (probs, extra, e(r.get("error", ""))) if x)
        rows.append(
            f'<div class="row {e(label)}" data-idx="{idx}" data-label="{e(label)}" '
            f'data-conf="{r["confidence"]}" data-low="{int(low)}">'
            f'<div class="top"><span class="id">#{e(str(c["id"]))}</span>'
            f'<span class="tag {e(label)}">{e(label)}</span>'
            f'<span class="conf">confidence {r["confidence"]:.2f}</span>'
            f'{"<span class=low>check me</span>" if low else ""}</div>'
            f'<div class="text">{e(c["text"])}</div>'
            f'<div class="meta">{meta}</div></div>'
        )
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    page = PAGE.format(title=f"Clef classification ({version})", model=model,
                       total=len(comments), counts=summary, thr=LOW_CONFIDENCE,
                       rows="\n".join(rows))
    open(out_path, "w", encoding="utf-8").write(page)


def run(version):
    cfg = CONFIGS[version]
    account, token = load_credentials()
    comments = load_comments(cfg["input"])
    print(f"[{version}] {len(comments)} comments from {cfg['input']} with {cfg['model']}...")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(
            lambda c: classify(c, version, cfg["model"], account, token), comments))
    errors = sum(r["label"] == "error" for r in results)
    render_html(version, cfg["model"], comments, results, cfg["output"])
    print(f"[{version}] wrote {cfg['output']}" + (f" ({errors} errors, re-run to retry)" if errors else ""))


if __name__ == "__main__":
    versions = sys.argv[1:] or ["v1", "v2"]
    for v in versions:
        if v not in CONFIGS:
            sys.exit(f"Unknown version '{v}'. Use v1 or v2.")
        run(v)