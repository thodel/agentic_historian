/*
 * search.js — client-side catalogue search (#223, P1-A3).
 *
 * Consumes search-index.json produced by scripts/build_index.py (#221). The
 * matching logic is the pure function `searchIndex(records, query)` so it can be
 * unit-tested under node without a DOM. Substring, case- and diacritic-
 * insensitive across the fields listed in SEARCHED_FIELDS. No dependencies, no
 * build step — GitHub Pages serves this file as-is.
 *
 * Dual export: CommonJS (node tests) + browser global (window.searchIndex). The
 * DOM wiring only runs when a `document` with the search box is present.
 */
(function (root) {
  "use strict";

  // Fields of a search-index record that the search reads. Kept in sync with
  // build_index.py's record shape by the #223 contract test. `url` is used for
  // the result link, not matched against, so it is intentionally absent here.
  var SEARCHED_FIELDS = ["doc_id", "date", "lang", "script", "entities", "snippet"];

  // Case- and diacritic-insensitive fold: "Müller" and "muller" compare equal.
  function foldDiacritics(s) {
    return String(s == null ? "" : s)
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase();
  }

  // Flatten the searched fields of one record into a single folded haystack.
  function recordHaystack(rec) {
    var parts = [];
    for (var i = 0; i < SEARCHED_FIELDS.length; i++) {
      var v = rec[SEARCHED_FIELDS[i]];
      if (Array.isArray(v)) {
        parts.push(v.join(" "));
      } else if (v != null) {
        parts.push(String(v));
      }
    }
    return foldDiacritics(parts.join(" "));
  }

  // Pure: return the records matching `query`. Empty/whitespace query → all
  // records (a copy). Otherwise every space-separated term must appear (AND).
  function searchIndex(records, query) {
    var recs = Array.isArray(records) ? records : [];
    var terms = foldDiacritics(query).split(/\s+/).filter(Boolean);
    if (terms.length === 0) return recs.slice();
    return recs.filter(function (rec) {
      var hay = recordHaystack(rec);
      for (var i = 0; i < terms.length; i++) {
        if (hay.indexOf(terms[i]) === -1) return false;
      }
      return true;
    });
  }

  var api = {
    searchIndex: searchIndex,
    foldDiacritics: foldDiacritics,
    recordHaystack: recordHaystack,
    SEARCHED_FIELDS: SEARCHED_FIELDS,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;            // node / tests
  } else {
    root.searchIndex = searchIndex;  // browser: window.searchIndex(...)
    root.AHSearch = api;
  }

  // ── browser DOM wiring (skipped under node) ──────────────────────────────
  if (typeof document === "undefined") return;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function renderResults(container, matches) {
    if (matches.length === 0) {
      container.innerHTML = '<p class="ah-search-empty">Keine Treffer.</p>';
      return;
    }
    var html = matches.map(function (rec) {
      var meta = [rec.date, rec.lang, rec.script].filter(Boolean).map(esc).join(" · ");
      var ents = (rec.entities || []).slice(0, 12).map(function (e) {
        return '<span class="ah-ent">' + esc(e) + "</span>";
      }).join(" ");
      return (
        '<li class="ah-hit">' +
          '<a class="ah-hit-title" href="' + esc(rec.url || "#") + '">' + esc(rec.doc_id) + "</a>" +
          (meta ? '<div class="ah-hit-meta">' + meta + "</div>" : "") +
          (rec.snippet ? '<p class="ah-hit-snippet">' + esc(rec.snippet) + "</p>" : "") +
          (ents ? '<div class="ah-hit-ents">' + ents + "</div>" : "") +
        "</li>"
      );
    }).join("");
    container.innerHTML = '<ul class="ah-hits">' + html + "</ul>";
  }

  function init() {
    var box = document.getElementById("ah-search-box");
    var results = document.getElementById("ah-search-results");
    var count = document.getElementById("ah-search-count");
    if (!box || !results) return;

    var records = [];
    var indexUrl = box.getAttribute("data-index") || "search-index.json";

    function run() {
      var matches = searchIndex(records, box.value);
      if (count) {
        count.textContent = box.value.trim()
          ? matches.length + " / " + records.length + " Treffer"
          : records.length + " Dokument(e)";
      }
      renderResults(results, matches);
    }

    fetch(indexUrl)
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        records = Array.isArray(data) ? data : [];
        box.disabled = false;
        box.placeholder = "Suche in " + records.length + " Dokument(en)…";
        run();
      })
      .catch(function (err) {
        results.innerHTML =
          '<p class="ah-search-empty">Suchindex nicht ladbar: ' + esc(err.message) + "</p>";
      });

    box.addEventListener("input", run);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof self !== "undefined" ? self : this);
