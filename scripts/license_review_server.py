#!/usr/bin/env python3
"""Local-only review UI for records the model flagged as license_confidence
== "flagged_for_review" -- a visual concern (a watermark or copyright mark
printed on the photo itself, since the 2026-09-06 prompt narrowing) the
VLM saw in the pixels, independent of what the deterministic license
string says.

Narrowed 2026-09-06 (see DECISIONS.md) from an earlier, broader version
that also showed every non-"Public domain/Full" record. Those no longer
need a per-record decision here: `nps_client.is_open_license()` already
settled that GrantingRights level isn't a real rights signal (a
"Public domain/Partial" record publishes exactly like "Full" does), and
a "Restrictions apply..." license is a hard, no-exceptions publish
exclusion (build_site.py, pipeline.py) with no override mechanism left
to approve into. Only flagged_for_review, on a record whose license
already clears is_open_license(), is still a real open question -- is
the model's flagged concern real or not?

As of 2026-09-06 (same day, a later change): build_site.py holds EVERY
flagged_for_review record off the site by default now, not just the
ones a human hides -- so a record here has exactly three states, and
the first two look the same on the live site (not published) but mean
different things:
  - Unreviewed (default): not yet looked at. Not published.
  - Confirmed: a human decided the flag is a false positive. Writes the
    id to confirmed_ids.json, which build_site.py treats as an override
    -- publishes despite the flag. confirmed_ids.json exists because
    license_confidence itself lives in data/catalog.json on wopr, not
    something editable in place from here; same override-file pattern
    as hidden_ids.json, opposite polarity (allowlist, not denylist).
  - Hidden: a human decided the flag is real, or just doesn't want the
    image published for any reason. Writes the id to hidden_ids.json,
    the same file dedup_review_server.py uses. Not published (same
    outcome as Unreviewed, but recorded as a reviewed decision, not an
    open question).
Click "Confirm" or "Hide" on a card to set that state; click the active
one again to reset to Unreviewed.

Reads data/license_review_candidates.json (a superset snapshot -- also
holds the now-non-actionable non-Full-license records from the earlier
version, for audit/reference, but api_records only serves the
flagged_for_review + currently-open-license subset). This is a per-
record call -- a human today, potentially an LLM given the same data
(photo + title + photographer + the model's own stated reason) later.

Never deployed, never touches docs/ or wopr. Binds to 0.0.0.0 (LAN-
reachable, no auth) -- switch back to host="127.0.0.1" below when done
reviewing if that's a concern.

Usage: uv run --extra dedup python scripts/license_review_server.py
"""

import json
import sys
from pathlib import Path

from flask import Flask, jsonify, request

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from vistarium.nps_client import is_open_license  # noqa: E402

CANDIDATES_PATH = REPO_ROOT / "data" / "license_review_candidates.json"
HIDDEN_IDS_PATH = REPO_ROOT / "hidden_ids.json"
CONFIRMED_IDS_PATH = REPO_ROOT / "confirmed_ids.json"

app = Flask(__name__, static_folder=str(REPO_ROOT / "docs" / "thumbs"), static_url_path="/thumbs")


def _read_ids(path: Path) -> set[str]:
    return set(json.loads(path.read_text())) if path.exists() else set()


def _write_ids(path: Path, ids: set[str]) -> None:
    path.write_text(json.dumps(sorted(ids), indent=2))


def _load_state() -> tuple[list[dict], set[str], set[str]]:
    records = json.loads(CANDIDATES_PATH.read_text()) if CANDIDATES_PATH.exists() else []
    return records, _read_ids(HIDDEN_IDS_PATH), _read_ids(CONFIRMED_IDS_PATH)


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
  .member { width: 380px; border: 4px solid transparent; border-radius: 8px; overflow: hidden; background: #1d1d1d; }
  .member img { display: block; width: 100%; height: 260px; object-fit: cover; }
  .member .label { padding: 12px 14px; font-size: 15px; line-height: 1.5; }
  .member.confirmed { border-color: #4caf50; }
  .member.hidden-choice { border-color: #e57373; opacity: 0.65; }
  .member .id { font-family: monospace; font-size: 12px; color: #888; }
  .member .title { color: #fff; font-size: 18px; font-weight: 600; margin-top: 4px; }
  .member .status { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; margin-top: 6px; }
  .member.confirmed .status { color: #4caf50; }
  .member.hidden-choice .status { color: #e57373; }
  .member.unreviewed .status { color: #999; }
  .actions { display: flex; gap: 8px; margin-top: 10px; }
  .actions button { flex: 1; padding: 8px; border-radius: 6px; border: 1px solid #444; background: #2a2a2a; color: #ccc; cursor: pointer; font-size: 13px; font-weight: 600; }
  .actions button:hover { background: #333; }
  .actions button.confirm.active { background: #4caf50; border-color: #4caf50; color: #111; }
  .actions button.hide.active { background: #e57373; border-color: #e57373; color: #111; }
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

function statusText(state) {
  if (state === 'confirmed') return 'Confirmed -- published despite flag';
  if (state === 'hidden') return 'Hidden -- not published';
  return 'Unreviewed -- not published';
}

async function load() {
  const res = await fetch('/api/records');
  const data = await res.json();
  const container = document.getElementById('groups');
  const counts = {confirmed: 0, hidden: 0, unreviewed: 0};
  for (const r of data.records) counts[r.state]++;
  document.getElementById('summary').textContent =
    `${data.records.length} flagged records -- ${counts.confirmed} confirmed, ${counts.hidden} hidden, ${counts.unreviewed} unreviewed (unreviewed and hidden are both currently unpublished).`;
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
          <div class="member ${m.state === 'confirmed' ? 'confirmed' : m.state === 'hidden' ? 'hidden-choice' : 'unreviewed'}" data-id="${m.id}">
            <img src="/thumbs/${m.thumb_file}" loading="lazy">
            <div class="label">
              <div class="id">${m.id}</div>
              <div class="title">${m.title}</div>
              <div class="status">${statusText(m.state)}</div>
              <div class="actions">
                <button class="confirm ${m.state === 'confirmed' ? 'active' : ''}" data-action="confirmed">Confirm</button>
                <button class="hide ${m.state === 'hidden' ? 'active' : ''}" data-action="hidden">Hide</button>
              </div>
              ${field('License Evidence (model)', m.license_evidence)}
              ${field('License', m.license)}
              ${field('Copyright', m.detail_copyright)}
              ${field('PhotoCredit', m.detail_photo_credit)}
              ${field('Constraints Information', m.detail_constraints_information)}
              ${field('Contacts', m.detail_contacts)}
              <a class="original-link" href="${m.source_url}" target="_blank" rel="noopener">View original on NPGallery &rarr;</a>
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
        const newState = wasActive ? 'unreviewed' : btn.dataset.action;
        await fetch('/api/decide', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({id, state: newState}),
        });
        el.className = 'member ' + (newState === 'confirmed' ? 'confirmed' : newState === 'hidden' ? 'hidden-choice' : 'unreviewed');
        el.querySelector('.status').textContent = statusText(newState);
        el.querySelectorAll('.actions button').forEach(b => b.classList.remove('active'));
        if (newState !== 'unreviewed') btn.classList.add('active');
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
    records, hidden, confirmed = _load_state()
    filtered = [
        r
        for r in records
        if r.get("license_confidence") == "flagged_for_review" and is_open_license(r["license"])
    ]
    filtered.sort(
        key=lambda r: (
            r["park"],
            r.get("detail_copyright") or "",
            r.get("detail_photo_credit") or "",
        )
    )
    out = []
    for r in filtered:
        if r["id"] in confirmed:
            state = "confirmed"
        elif r["id"] in hidden:
            state = "hidden"
        else:
            state = "unreviewed"
        out.append(
            {
                "id": r["id"],
                "thumb_file": Path(r["thumb"]).name,
                "title": r["title"],
                "park": r["park"],
                "license": r["license"],
                "license_evidence": r.get("license_evidence"),
                "source_url": r["source_url"],
                "detail_copyright": r.get("detail_copyright"),
                "detail_photo_credit": r.get("detail_photo_credit"),
                "detail_constraints_information": r.get("detail_constraints_information"),
                "detail_contacts": r.get("detail_contacts"),
                "state": state,
            }
        )
    return jsonify({"records": out})


@app.route("/api/decide", methods=["POST"])
def api_decide():
    body = request.get_json()
    photo_id = body["id"]
    state = body["state"]
    if state not in ("confirmed", "hidden", "unreviewed"):
        return jsonify({"ok": False, "error": "invalid state"}), 400

    hidden = _read_ids(HIDDEN_IDS_PATH)
    confirmed = _read_ids(CONFIRMED_IDS_PATH)
    hidden.discard(photo_id)
    confirmed.discard(photo_id)
    if state == "confirmed":
        confirmed.add(photo_id)
    elif state == "hidden":
        hidden.add(photo_id)
    _write_ids(HIDDEN_IDS_PATH, hidden)
    _write_ids(CONFIRMED_IDS_PATH, confirmed)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5152, debug=False)
