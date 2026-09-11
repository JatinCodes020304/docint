/**
 * dashboard.js — list of processed documents
 *
 * "Get data" (DocIntelAPI.getDocuments) is separate from "draw the table"
 * (renderDocuments). When the backend exists you only change mock-data.js.
 */
(function () {
  "use strict";

  const root = document.getElementById("dashboard-root");
  const fmt = window.DocIntelFormat;

  function statusBadge(status) {
    const isPass = status === "PASS";
    const cls = isPass ? "badge badge-pass" : "badge badge-fail";
    const label = isPass ? "PASS" : "FAILED";
    return '<span class="' + cls + '">' + label + "</span>";
  }

  function renderDocuments(docs) {
    if (!docs.length) {
      root.innerHTML =
        '<p class="empty">No documents yet. <a href="/upload.html">Upload one</a>.</p>';
      return;
    }

    const rows = docs
      .map(function (doc) {
        const href = fmt.resultHref(doc.document_name);
        return (
          "<tr class=\"clickable-row\" tabindex=\"0\" data-href=\"" +
          href +
          "\">" +
          "<td>" +
          escapeHtml(doc.document_name) +
          "</td>" +
          "<td>" +
          escapeHtml(fmt.documentTypeLabel(doc.document_type)) +
          "</td>" +
          "<td>" +
          statusBadge(doc.processing_status) +
          "</td>" +
          '<td class="num">' +
          escapeHtml(fmt.formatDate(doc.processed_at)) +
          "</td>" +
          "</tr>"
        );
      })
      .join("");

    root.innerHTML =
      '<div class="table-wrap">' +
      "<table>" +
      "<thead><tr>" +
      "<th>Document name</th>" +
      "<th>Type</th>" +
      "<th>Status</th>" +
      "<th>Processed</th>" +
      "</tr></thead>" +
      "<tbody>" +
      rows +
      "</tbody></table></div>";

    // Whole-row click (and Enter) → result page
    root.querySelectorAll(".clickable-row").forEach(function (row) {
      row.addEventListener("click", function () {
        window.location.href = row.getAttribute("data-href");
      });
      row.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          window.location.href = row.getAttribute("data-href");
        }
      });
    });
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "\u0026amp;")
      .replace(/</g, "\u0026lt;")
      .replace(/>/g, "\u0026gt;")
      .replace(/"/g, "\u0026#34;");
  }

  window.DocIntelAPI.getDocuments()
    .then(renderDocuments)
    .catch(function (err) {
      root.innerHTML =
        '<div class="alert alert-error is-visible" role="alert">' +
        escapeHtml((err && err.message) || "Could not load documents.") +
        "</div>";
    });
})();
