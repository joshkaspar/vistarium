#!/usr/bin/env python3
"""Local-only review UI for records whose license isn't unambiguously
open-access.

Modeled directly on dedup_review_server.py. Only "Public domain/Full" is
treated as safe by default (see build_site.py's FULLY_OPEN_LICENSE) --
everything else ("Public domain/Unknown", "Public domain/Partial",
"Restrictions apply...", etc.) is excluded from the published site
unless its id is explicitly approved here. Investigated live 2026-09-05
(see DECISIONS.md): "Partial" covers real, distinct problems (an
explicit "IN COPYRIGHT ... no public distribution" override NPS's own
metadata carries in a field this pipeline never captured, and photo-
contest submissions where the entrant keeps copyright) while "Unknown"
is a genuine mixed bag -- legitimate NPS staff photography sitting next
to real uncertainty, with no further deterministic signal available.
This is a per-record call -- a human today, potentially an LLM given the
same data (photo + title + photographer + license string) later.

Click a thumbnail to toggle approved/excluded -- saves to
license_approved_ids.json immediately, nothing is ever deleted from
data/catalog.json. The "View original" link opens the NPGallery
AssetDetail page directly (source_url) -- click on the link itself
doesn't toggle the card, it opens in a new tab.

Reads data/license_review_candidates.json, a frozen snapshot (not
docs/data.json directly) -- deliberately: once build_site.py's
license-approval gate is deployed and a rebuild runs, every unapproved
non-Full-license record disappears from docs/data.json immediately,
which would leave nothing left to review. The snapshot was generated
once (2026-09-05, 923 records) from docs/data.json before that gate
existed; it doesn't need to be regenerated for this review pass to
finish, since it's the fixed list of records this review is deciding.
scripts/fetch_license_review_details.py enriches that same snapshot
in place with the extra rights fields this page shows (Copyright,
Constraints Information, Access Constraints, Contacts, Photographer) --
until that finishes for a given record, those fields show as blank,
not an error.

Grouped by park, then sorted within each park by (Copyright,
Photographer) so records sharing the same rights-holder/credit cluster
together -- e.g. every Tim Hauf/Channel Islands record ends up
adjacent, since they carry the same override text.

Never deployed, never touches docs/ or wopr. Binds to 0.0.0.0 (LAN-
reachable, no auth) -- switch back to host="127.0.0.1" below when done
reviewing if that's a concern.

Usage: uv run --extra dedup python scripts/license_review_server.py
"""

import json
from pathlib import Path

from flask import Flask, jsonify, request

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = REPO_ROOT / "data" / "license_review_candidates.json"
LICENSE_APPROVED_PATH = REPO_ROOT / "license_approved_ids.json"
FULLY_OPEN_LICENSE = "Public domain/Full"

app = Flask(__name__, static_folder=str(REPO_ROOT / "docs" / "thumbs"), static_url_path="/thumbs")


def _load_state() -> tuple[list[dict], set[str]]:
    records = json.loads(CANDIDATES_PATH.read_text()) if CANDIDATES_PATH.exists() else []
    approved = (
        set(json.loads(LICENSE_APPROVED_PATH.read_text()))
        if LICENSE_APPROVED_PATH.exists()
        else set()
    )
    return records, approved


PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>License review</title>
<style>
  body { font-family: system-ui, sans-serif; background: #1a1a1a; color: #eee; margin: 0; padding: 28px; font-size: 16px; }
  h1 { font-size: 26px; font-weight: 600; }
  .summary { color: #aaa; margin-bottom: 28px; font-size: 16px; }
  .group { background: #242424; border-radius: 10px; padding: 20px; margin-bottom: 26px; }
  .group-header { font-size: 20px; font-weight: 600; color: #ffb74d; margin-bottom: 16px; }
  .members { display: flex; gap: 18px; flex-wrap: wrap; }
  .member { width: 380px; cursor: pointer; border: 4px solid transparent; border-radius: 8px; overflow: hidden; background: #1d1d1d; }
  .member img { display: block; width: 100%; height: 260px; object-fit: cover; }
  .member .label { padding: 12px 14px; font-size: 15px; line-height: 1.5; }
  .member.approved { border-color: #4caf50; }
  .member.excluded { border-color: #555; opacity: 0.65; }
  .member .id { font-family: monospace; font-size: 12px; color: #888; }
  .member .title { color: #fff; font-size: 18px; font-weight: 600; margin-top: 4px; }
  .field { margin-top: 8px; }
  .field .k { color: #999; font-size: 12px; text-transform: uppercase; letter-spacing: 0.03em; }
  .field .v { color: #eee; white-space: pre-line; }
  .field .v.empty { color: #666; font-style: italic; }
  .original-link { display: inline-block; margin-top: 10px; color: #64b5f6; text-decoration: none; font-size: 14px; }
  .original-link:hover { text-decoration: underline; }
  .empty { color: #888; padding: 40px; text-align: center; }
</style>
</head>
<body>
<h1>License review</h1>
<div class="summary" id="summary"></div>
<div id="groups"></div>
<script>
function field(label, value) {
  const v = value ? value : '(not fetched yet)';
  const cls = value ? 'v' : 'v empty';
  return `<div class="field"><div class="k">${label}</div><div class="${cls}">${v}</div></div>`;
}

async function load() {
  const res = await fetch('/api/records');
  const data = await res.json();
  const container = document.getElementById('groups');
  const approvedCount = data.records.filter(r => r.approved).length;
  document.getElementById('summary').textContent =
    `${data.records.length} records need a per-record call -- ${approvedCount} approved so far. Click a card to toggle approved/excluded.`;
  if (data.records.length === 0) {
    container.innerHTML = '<div class="empty">Nothing to review.</div>';
    return;
  }
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
          <div class="member ${m.approved ? 'approved' : 'excluded'}" data-id="${m.id}">
            <img src="/thumbs/${m.thumb_file}" loading="lazy">
            <div class="label">
              <div class="id">${m.id}</div>
              <div class="title">${m.title}</div>
              ${field('License', m.license)}
              ${field('Asset ID', m.id)}
              ${field('Copyright', m.detail_copyright)}
              ${field('PhotoCredit', m.detail_photo_credit)}
              ${field('Constraints Information', m.detail_constraints_information)}
              ${field('Access Constraints', m.detail_access_constraints)}
              ${field('Contacts', m.detail_contacts)}
              <a class="original-link" href="${m.source_url}" target="_blank" rel="noopener">View original on NPGallery &rarr;</a>
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');

  container.querySelectorAll('.member').forEach(el => {
    el.addEventListener('click', async (evt) => {
      if (evt.target.closest('a')) return;
      const id = el.dataset.id;
      const nowApproved = !el.classList.contains('approved');
      await fetch('/api/decide', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id, approved: nowApproved}),
      });
      el.classList.toggle('approved');
      el.classList.toggle('excluded');
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
    records, approved = _load_state()
    filtered = [r for r in records if r["license"] != FULLY_OPEN_LICENSE]
    filtered.sort(
        key=lambda r: (
            r["park"],
            r.get("detail_copyright") or "",
            r.get("detail_photo_credit") or "",
        )
    )
    out = [
        {
            "id": r["id"],
            "thumb_file": Path(r["thumb"]).name,
            "title": r["title"],
            "park": r["park"],
            "license": r["license"],
            "source_url": r["source_url"],
            "detail_copyright": r.get("detail_copyright"),
            "detail_photo_credit": r.get("detail_photo_credit"),
            "detail_constraints_information": r.get("detail_constraints_information"),
            "detail_access_constraints": r.get("detail_access_constraints"),
            "detail_contacts": r.get("detail_contacts"),
            "approved": r["id"] in approved,
        }
        for r in filtered
    ]
    return jsonify({"records": out})


@app.route("/api/decide", methods=["POST"])
def api_decide():
    body = request.get_json()
    photo_id = body["id"]
    approved = (
        set(json.loads(LICENSE_APPROVED_PATH.read_text()))
        if LICENSE_APPROVED_PATH.exists()
        else set()
    )
    if body["approved"]:
        approved.add(photo_id)
    else:
        approved.discard(photo_id)
    LICENSE_APPROVED_PATH.write_text(json.dumps(sorted(approved), indent=2))
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5152, debug=False)
