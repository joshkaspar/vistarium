(function () {
  "use strict";

  let records = [];

  const gallery = document.getElementById("gallery");
  const resultCount = document.getElementById("result-count");
  const sortSelect = document.getElementById("sort-by");
  const sortNote = document.getElementById("sort-note");
  const parkSelect = document.getElementById("filter-park");
  const timeSelect = document.getElementById("filter-time");
  const peopleSelect = document.getElementById("filter-people");
  const colorSelect = document.getElementById("filter-color");
  const dominantColorSelect = document.getElementById("filter-dominant-color");
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

  const SORTERS = {
    // Nulls (not-yet-scored / no source date) always sort last, regardless
    // of direction -- see build_site.py's _date_sortable for why ~65% of
    // records have no date at all.
    aesthetic: (a, b) => {
      if (a.aesthetic_score == null) return b.aesthetic_score == null ? 0 : 1;
      if (b.aesthetic_score == null) return -1;
      return b.aesthetic_score - a.aesthetic_score;
    },
    date: (a, b) => {
      if (a.date_sortable == null) return b.date_sortable == null ? 0 : 1;
      if (b.date_sortable == null) return -1;
      return b.date_sortable < a.date_sortable ? -1 : b.date_sortable > a.date_sortable ? 1 : 0;
    },
  };

  function matches(record) {
    if (parkSelect.value && record.park !== parkSelect.value) return false;
    if (timeSelect.value && record.time_of_day !== timeSelect.value) return false;
    if (peopleSelect.value && record.people_prominence !== peopleSelect.value) return false;
    if (colorSelect.value && record.color_mode !== colorSelect.value) return false;
    if (dominantColorSelect.value && record.dominant_color !== dominantColorSelect.value) return false;
    const tagQuery = tagInput.value.trim().toLowerCase();
    if (tagQuery && !record.tags.some((t) => t.toLowerCase().includes(tagQuery))) return false;
    return true;
  }

  // Must match main#gallery's grid-auto-rows and gap in style.css.
  const MASONRY_ROW_PX = 10;
  const MASONRY_GAP_PX = 16;

  function applyMasonrySpans() {
    // Two passes (measure all, then write all) so setting one card's
    // grid-row-end span can't force a layout recalc that skews the next
    // card's measurement -- a card's height depends only on its own
    // width/aspect-ratio, never on other cards' spans, so this is safe.
    const cards = Array.from(gallery.children);
    const spans = cards.map((card) =>
      Math.ceil((card.getBoundingClientRect().height + MASONRY_GAP_PX) / (MASONRY_ROW_PX + MASONRY_GAP_PX))
    );
    cards.forEach((card, i) => {
      card.style.gridRowEnd = `span ${spans[i]}`;
    });
  }

  function render() {
    const sorter = SORTERS[sortSelect.value] || SORTERS.aesthetic;
    const filtered = records.filter(matches).sort(sorter);
    sortNote.classList.toggle("hidden", sortSelect.value !== "aesthetic");
    resultCount.textContent = `${filtered.length} of ${records.length}`;
    gallery.innerHTML = "";
    for (const record of filtered) {
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `
        <img src="${record.thumb}" alt="${escapeHtml(record.title)}" loading="lazy"
             style="aspect-ratio: ${record.aspect || "16/9"}" />
        <div class="card-meta">${escapeHtml(record.park)} &middot; ${escapeHtml(record.time_of_day)}</div>
      `;
      card.addEventListener("click", () => openLightbox(record));
      gallery.appendChild(card);
    }
    applyMasonrySpans();
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

  [sortSelect, parkSelect, timeSelect, peopleSelect, colorSelect, dominantColorSelect].forEach((el) =>
    el.addEventListener("change", render)
  );
  tagInput.addEventListener("input", render);

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(applyMasonrySpans, 150);
  });

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
