(function () {
  "use strict";

  let records = [];

  const gallery = document.getElementById("gallery");
  const resultCount = document.getElementById("result-count");
  const parkSelect = document.getElementById("filter-park");
  const timeSelect = document.getElementById("filter-time");
  const peopleSelect = document.getElementById("filter-people");
  const tagInput = document.getElementById("filter-tag");
  const lightbox = document.getElementById("lightbox");
  const lightboxImg = document.getElementById("lightbox-img");
  const lightboxInfo = document.getElementById("lightbox-info");
  const lightboxClose = document.getElementById("lightbox-close");

  function populateParks() {
    const parks = [...new Set(records.map((r) => r.park))].sort();
    for (const park of parks) {
      const opt = document.createElement("option");
      opt.value = park;
      opt.textContent = park;
      parkSelect.appendChild(opt);
    }
  }

  function matches(record) {
    if (parkSelect.value && record.park !== parkSelect.value) return false;
    if (timeSelect.value && record.time_of_day !== timeSelect.value) return false;
    if (peopleSelect.value && record.people_prominence !== peopleSelect.value) return false;
    const tagQuery = tagInput.value.trim().toLowerCase();
    if (tagQuery && !record.tags.some((t) => t.toLowerCase().includes(tagQuery))) return false;
    return true;
  }

  function render() {
    const filtered = records.filter(matches);
    resultCount.textContent = `${filtered.length} of ${records.length}`;
    gallery.innerHTML = "";
    for (const record of filtered) {
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `
        <img src="${record.thumb}" alt="${escapeHtml(record.title)}" loading="lazy" />
        <div class="card-meta">${escapeHtml(record.park)} &middot; ${escapeHtml(record.time_of_day)}</div>
      `;
      card.addEventListener("click", () => openLightbox(record));
      gallery.appendChild(card);
    }
  }

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s == null ? "" : s;
    return div.innerHTML;
  }

  function openLightbox(record) {
    lightboxImg.src = record.thumb;
    lightboxImg.alt = record.title;
    const parts = [
      `<strong>${escapeHtml(record.title)}</strong>`,
      [record.park, record.date, record.time_of_day].filter(Boolean).map(escapeHtml).join(" &middot; "),
      record.photographer ? `Photo: ${escapeHtml(record.photographer)}` : "",
      `License: ${escapeHtml(record.license)}${record.license_confidence === "flagged_for_review" ? " (flagged for manual review)" : ""}`,
      record.tags.length ? escapeHtml(record.tags.join(", ")) : "",
      `<a href="${record.source_url}" target="_blank" rel="noopener">View source &amp; full resolution &rarr;</a>`,
    ].filter(Boolean);
    lightboxInfo.innerHTML = parts.join("<br>");
    lightbox.classList.remove("hidden");
  }

  function closeLightbox() {
    lightbox.classList.add("hidden");
    lightboxImg.src = "";
  }

  lightboxClose.addEventListener("click", closeLightbox);
  lightbox.addEventListener("click", (e) => {
    if (e.target === lightbox) closeLightbox();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeLightbox();
  });

  [parkSelect, timeSelect, peopleSelect].forEach((el) => el.addEventListener("change", render));
  tagInput.addEventListener("input", render);

  fetch("data.json")
    .then((r) => r.json())
    .then((data) => {
      records = data;
      populateParks();
      render();
    })
    .catch((err) => {
      gallery.innerHTML = `<p style="color:#b00">Couldn't load data.json: ${escapeHtml(String(err))}</p>`;
    });
})();
