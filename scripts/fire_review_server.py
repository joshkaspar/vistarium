#!/usr/bin/env python3
"""Local-only review UI for published records tagged with a fire/smoke
keyword (wildfire, prescribed burn, volcanic eruption glow, etc.) --
207 as of 2026-09-07's snapshot.

Purpose, explicitly: NOT a publish/hide decision tool by itself. Josh
asked whether these can be filtered deterministically or by the model --
some are genuinely striking scenic photography (Hawaii Volcanoes' lava
glow is that park's defining feature; a smoke plume over a lake is a
real landscape scene), others are operational fire-management
documentation (a hoseline, firefighting equipment prominent in frame)
that happens to have also been classified primary_subject: landscape
(every record here already cleared that bar, or it wouldn't be
published at all -- so primary_subject alone evidently doesn't
separate the two cases). No album triage ever saw most of these either
-- checked live 2026-09-07: ee9c9992 (Yellowstone, "Druid Complex") has
RelatedCollections ["NPS.gov Collection"], not a real curated album
(list_park_albums('YELL') has 266 real albums, none matching that
name) -- it's NPS's generic catch-all tag, not something
album_keywords.json ever gets a chance to filter on.

Decisions here write to data/fire_review_decisions.json (id -> "keep" |
"hide"), NOT directly to hidden_ids.json -- this tool does not touch
the live site by itself.

As of 2026-09-08: pre-seeded from a real model pass. model_client.py
gained fire_smoke_category (an enum, not a boolean, so the specific
reason stays inspectable -- see schema.json/DECISIONS.md), validated
against 11 hand-labeled examples (10/11 correct on the actual keep/
reject decision) before being run across all 207 of these candidates.
Every card's initial Keep/Hide state matches the model's call
(reject-shaped categories -- response_documentation,
volcanic_destruction, burn_aftermath_dominant -- default to Hide,
everything else to Keep), shown alongside the model's own category and
one-sentence evidence so a human can confirm or override each one.
Clicking Keep/Hide overwrites that seed for that record; nothing here
is a passive display, every card's current state is what will
actually get used once reviewed.

Thumbnails render with object-fit: contain (see DECISIONS.md,
2026-09-07) -- full frame always visible, never cropped by the tool.

Never deployed, never touches docs/ or wopr. Binds to 0.0.0.0 (LAN-
reachable, no auth) -- switch back to host="127.0.0.1" below when done.

Usage: uv run --extra dedup python scripts/fire_review_server.py
"""

import json
from pathlib import Path

from flask import Flask, jsonify, request

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = REPO_ROOT / "data" / "fire_review_candidates.json"
DECISIONS_PATH = REPO_ROOT / "data" / "fire_review_decisions.json"

app = Flask(__name__, static_folder=str(REPO_ROOT / "docs" / "thumbs"), static_url_path="/thumbs")


def _load_decisions() -> dict[str, str]:
    return json.loads(DECISIONS_PATH.read_text()) if DECISIONS_PATH.exists() else {}


def _write_decisions(decisions: dict[str, str]) -> None:
    DECISIONS_PATH.write_text(json.dumps(decisions, indent=2, sort_keys=True))


PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Fire/smoke review</title>
<style>
  body { font-family: system-ui, sans-serif; background: #1a1a1a; color: #eee; margin: 0; padding: 28px; font-size: 16px; }
  h1 { font-size: 26px; font-weight: 600; }
  .summary { color: #aaa; margin-bottom: 28px; font-size: 16px; }
  .group { background: #242424; border-radius: 10px; padding: 20px; margin-bottom: 26px; }
  .group-header { font-size: 20px; font-weight: 600; color: #ffb74d; margin-bottom: 16px; }
  .members { display: flex; gap: 18px; flex-wrap: wrap; }
  .member { width: 380px; border: 4px solid transparent; border-radius: 8px; overflow: hidden; background: #1d1d1d; }
  .member img { display: block; width: 100%; height: 500px; object-fit: contain; background: #000; }
  .member .label { padding: 12px 14px; font-size: 15px; line-height: 1.5; }
  .member.keep { border-color: #4caf50; }
  .member.hide-choice { border-color: #e57373; opacity: 0.65; }
  .member .id { font-family: monospace; font-size: 12px; color: #888; }
  .member .title { color: #fff; font-size: 18px; font-weight: 600; margin-top: 4px; }
  .member .status { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; margin-top: 6px; }
  .member.keep .status { color: #4caf50; }
  .member.hide-choice .status { color: #e57373; }
  .member.undecided .status { color: #999; }
  .actions { display: flex; gap: 8px; margin-top: 10px; }
  .actions button { flex: 1; padding: 8px; border-radius: 6px; border: 1px solid #444; background: #2a2a2a; color: #ccc; cursor: pointer; font-size: 13px; font-weight: 600; }
  .actions button:hover { background: #333; }
  .actions button.keep-btn.active { background: #4caf50; border-color: #4caf50; color: #111; }
  .actions button.hide-btn.active { background: #e57373; border-color: #e57373; color: #111; }
  .field { margin-top: 8px; }
  .field .k { color: #999; font-size: 12px; text-transform: uppercase; letter-spacing: 0.03em; }
  .field .v { color: #eee; white-space: pre-line; }
  .tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
  .tag { background: #333; color: #ddd; font-size: 12px; padding: 2px 8px; border-radius: 10px; }
  .model-category { display: inline-block; font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 10px; margin-top: 8px; }
  .model-category.reject { background: #4a2020; color: #ff8a80; }
  .model-category.keep { background: #1f3a24; color: #81c784; }
</style>
</head>
<body>
<h1>Fire/smoke review</h1>
<div class="summary" id="summary"></div>
<div id="groups"></div>
<script>
function field(label, value) {
  return `<div class="field"><div class="k">${label}</div><div class="v">${value ?? ''}</div></div>`;
}

function statusText(state) {
  if (state === 'keep') return 'Keep';
  if (state === 'hide') return 'Hide';
  return 'Undecided';
}

const REJECT_CATEGORIES = new Set(['response_documentation', 'volcanic_destruction', 'burn_aftermath_dominant']);

async function load() {
  const res = await fetch('/api/records');
  const data = await res.json();
  const container = document.getElementById('groups');
  const counts = {keep: 0, hide: 0, undecided: 0};
  for (const r of data.records) counts[r.state]++;
  document.getElementById('summary').textContent =
    `${data.records.length} fire/smoke-tagged records -- ${counts.keep} keep, ${counts.hide} hide, ${counts.undecided} undecided (pre-seeded from the model's fire_smoke_category call; click Keep/Hide to override). Decisions save to data/fire_review_decisions.json, not hidden_ids.json.`;
  const groups = [];
  let current = null;
  for (const r of data.records) {
    if (!current || current.park !== r.park) {
      current = { park: r.park, members: [] };
      groups.push(current);
    }
    current.members.push(r);
  }
  container.innerHTML = groups.map(g => `
    <div class="group">
      <div class="group-header">${g.park} &middot; ${g.members.length} records</div>
      <div class="members">
        ${g.members.map(m => `
          <div class="member ${m.state === 'keep' ? 'keep' : m.state === 'hide' ? 'hide-choice' : 'undecided'}" data-id="${m.id}">
            <img src="/thumbs/${m.thumb_file}" loading="lazy">
            <div class="label">
              <div class="id">${m.id}</div>
              <div class="title">${m.title}</div>
              <div class="status">${statusText(m.state)}</div>
              <div class="actions">
                <button class="keep-btn ${m.state === 'keep' ? 'active' : ''}" data-action="keep">Keep</button>
                <button class="hide-btn ${m.state === 'hide' ? 'active' : ''}" data-action="hide">Hide</button>
              </div>
              <span class="model-category ${m.model_category && REJECT_CATEGORIES.has(m.model_category) ? 'reject' : 'keep'}">${m.model_category ?? 'not classified'}</span>
              ${field('Model evidence', m.model_evidence)}
              ${field('Tags', '')}
              <div class="tags">${m.tags.map(t => `<span class="tag">${t}</span>`).join('')}</div>
              ${field('People prominence', m.people_prominence)}
              ${field('Photographer', m.photographer)}
              ${field('Aesthetic score', m.aesthetic_score)}
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');

  container.querySelectorAll('.member').forEach(el => {
    el.querySelectorAll('.actions button').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = el.dataset.id;
        const wasActive = btn.classList.contains('active');
        const newState = wasActive ? 'undecided' : btn.dataset.action;
        await fetch('/api/decide', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({id, state: newState}),
        });
        el.className = 'member ' + (newState === 'keep' ? 'keep' : newState === 'hide' ? 'hide-choice' : 'undecided');
        el.querySelector('.status').textContent = statusText(newState);
        el.querySelectorAll('.actions button').forEach(b => b.classList.remove('active'));
        if (newState !== 'undecided') btn.classList.add('active');
      });
    });
  });
}
load();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return PAGE


@app.route("/api/records")
def api_records():
    records = json.loads(CANDIDATES_PATH.read_text()) if CANDIDATES_PATH.exists() else []
    decisions = _load_decisions()
    records = sorted(records, key=lambda r: (r["park"], r.get("model_category") or ""))
    out = [
        {
            "id": r["id"],
            "thumb_file": Path(r["thumb"]).name,
            "title": r["title"],
            "park": r["park"],
            "tags": r.get("tags", []),
            "people_prominence": r.get("people_prominence"),
            "photographer": r.get("photographer"),
            "aesthetic_score": r.get("aesthetic_score"),
            "model_category": r.get("model_category"),
            "model_evidence": r.get("model_evidence"),
            "state": decisions.get(r["id"], "undecided"),
        }
        for r in records
    ]
    return jsonify({"records": out})


@app.route("/api/decide", methods=["POST"])
def api_decide():
    body = request.get_json()
    photo_id = body["id"]
    state = body["state"]
    if state not in ("keep", "hide", "undecided"):
        return jsonify({"ok": False, "error": "invalid state"}), 400

    decisions = _load_decisions()
    if state == "undecided":
        decisions.pop(photo_id, None)
    else:
        decisions[photo_id] = state
    _write_decisions(decisions)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5153, debug=False)
