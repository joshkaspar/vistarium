#!/usr/bin/env python3
"""Local-only review UI for duplicate clusters found by find_duplicates.py.

Shows each cluster's member photos side by side; the suggested keeper
(highest aesthetic_score) starts checked as "keep," every other member
starts hidden. Click a thumbnail to toggle keep/hide -- saves to
hidden_ids.json immediately, nothing is ever deleted.

Never deployed, never touches docs/ or wopr -- binds to localhost only.

Usage: uv run --extra dedup python scripts/dedup_review_server.py
"""

import json
from pathlib import Path

from flask import Flask, jsonify, request

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = REPO_ROOT / "docs" / "data.json"
CLUSTERS_PATH = REPO_ROOT / "data" / "dedup_clusters.json"
HIDDEN_IDS_PATH = REPO_ROOT / "hidden_ids.json"

app = Flask(__name__, static_folder=str(REPO_ROOT / "docs" / "thumbs"), static_url_path="/thumbs")


def _load_state() -> tuple[list[dict], dict[str, dict], set[str]]:
    clusters = json.loads(CLUSTERS_PATH.read_text()) if CLUSTERS_PATH.exists() else []
    records = {r["id"]: r for r in json.loads(DATA_JSON.read_text())}
    hidden = set(json.loads(HIDDEN_IDS_PATH.read_text())) if HIDDEN_IDS_PATH.exists() else set()
    return clusters, records, hidden


PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Duplicate review</title>
<style>
  body { font-family: system-ui, sans-serif; background: #1a1a1a; color: #eee; margin: 0; padding: 24px; }
  h1 { font-size: 20px; font-weight: 600; }
  .summary { color: #aaa; margin-bottom: 24px; }
  .cluster { background: #242424; border-radius: 8px; padding: 16px; margin-bottom: 20px; }
  .cluster-header { font-size: 13px; color: #999; margin-bottom: 10px; }
  .members { display: flex; gap: 12px; flex-wrap: wrap; }
  .member { width: 220px; cursor: pointer; border: 3px solid transparent; border-radius: 6px; overflow: hidden; }
  .member img { display: block; width: 100%; height: 140px; object-fit: cover; }
  .member .label { padding: 6px 8px; font-size: 12px; }
  .member.kept { border-color: #4caf50; }
  .member.hidden { border-color: #333; opacity: 0.45; }
  .member .id { font-family: monospace; font-size: 10px; color: #888; }
  .member .score { color: #ffb74d; }
  .empty { color: #888; padding: 40px; text-align: center; }
</style>
</head>
<body>
<h1>Duplicate review</h1>
<div class="summary" id="summary"></div>
<div id="clusters"></div>
<script>
async function load() {
  const res = await fetch('/api/clusters');
  const data = await res.json();
  const container = document.getElementById('clusters');
  document.getElementById('summary').textContent =
    `${data.clusters.length} clusters -- click any thumbnail to toggle keep/hide`;
  if (data.clusters.length === 0) {
    container.innerHTML = '<div class="empty">No clusters. Run find_duplicates.py first.</div>';
    return;
  }
  container.innerHTML = data.clusters.map((c, ci) => `
    <div class="cluster">
      <div class="cluster-header">${c.park} &middot; ${c.type} match</div>
      <div class="members">
        ${c.members.map(m => `
          <div class="member ${m.hidden ? 'hidden' : 'kept'}" data-id="${m.id}" data-cluster="${ci}">
            <img src="/thumbs/${m.thumb_file}" loading="lazy">
            <div class="label">
              <div class="id">${m.id.slice(0, 8)}</div>
              <div class="score">score: ${m.aesthetic_score ?? 'n/a'}</div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');

  container.querySelectorAll('.member').forEach(el => {
    el.addEventListener('click', async () => {
      const id = el.dataset.id;
      const nowHidden = el.classList.contains('kept');
      await fetch('/api/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id, hidden: nowHidden}),
      });
      el.classList.toggle('kept');
      el.classList.toggle('hidden');
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


@app.route("/api/clusters")
def api_clusters():
    clusters, records, hidden = _load_state()
    out = []
    for c in clusters:
        members = []
        for mid in c["member_ids"]:
            r = records.get(mid)
            if r is None:
                continue
            members.append(
                {
                    "id": mid,
                    "thumb_file": Path(r["thumb"]).name,
                    "aesthetic_score": r.get("aesthetic_score"),
                    "hidden": mid in hidden,
                }
            )
        out.append({"park": c["park"], "type": c["type"], "members": members})
    return jsonify({"clusters": out})


@app.route("/api/toggle", methods=["POST"])
def api_toggle():
    body = request.get_json()
    photo_id = body["id"]
    hidden = set(json.loads(HIDDEN_IDS_PATH.read_text())) if HIDDEN_IDS_PATH.exists() else set()
    if body["hidden"]:
        hidden.add(photo_id)
    else:
        hidden.discard(photo_id)
    HIDDEN_IDS_PATH.write_text(json.dumps(sorted(hidden), indent=2))
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5151, debug=False)
