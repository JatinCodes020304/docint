/**
 * result.js — one document's full result
 *
 * Reads ?name= from the URL, asks DocIntelAPI.getDocument(name), then
 * renders extracted fields, line items, validation checks, and raw JSON.
 */
(function () {
  "use strict";

  const root = document.getElementById("result-root");
  const titleEl = document.getElementById("doc-title");
  const subtitleEl = document.getElementById("doc-subtitle");
  const statusEl = document.getElementById("doc-status");
  const fmt = window.DocIntelFormat;

  const params = new URLSearchParams(window.location.search);
  const documentName = params.get("name");

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "\u0026amp;")
      .replace(/</g, "\u0026lt;")
      .replace(/>/g, "\u0026gt;")
      .replace(/"/g, "\u0026#34;");
  }

  function statusBadge(status) {
    const normalized = String(status || "").toUpperCase();
    const pass = normalized === "PASS";
    const cls = pass ? "badge badge-pass" : "badge badge-fail";
    const label = pass ? "PASS" : normalized || "UNKNOWN";
    return '<span class="' + cls + '">' + escapeHtml(label) + "</span>";
  }

  function yesNo(value) {
    if (value === true) return "Yes";
    if (value === false) return "No";
    return "—";
  }

  function renderField(key, field) {
    const missing = field == null || field.value === null || field.value === undefined;
    const low = !missing && fmt.isLowConfidence(field.confidence);
    const classes = ["kv"];
    if (missing) classes.push("is-missing");
    if (low) classes.push("is-low-confidence");

    let valueHtml;
    if (missing) {
      valueHtml = '<span class="missing-flag">Missing / unreadable</span>';
    } else if (field.value && typeof field.value === "object") {
      valueHtml = Object.keys(field.value)
        .map(function (k) {
          return '<div><strong>' + escapeHtml(k) + ':</strong> ' + escapeHtml(fmt.formatNumber(field.value[k]) || "—") + '</div>';
        })
        .join("");
    } else {
      valueHtml = escapeHtml(fmt.formatNumber(field.value));
    }

    const bits = [];
    const conf = fmt.formatConfidence(field.confidence);
    if (conf) {
      bits.push(
        low
          ? 'Confidence ' + conf + " (low)"
          : "Confidence " + conf
      );
    }
    if (field.page_number != null) bits.push("Page " + field.page_number);

    return (
      '<div class="' +
      classes.join(" ") +
      '">' +
      '<span class="kv-label">' +
      escapeHtml(fmt.humanizeKey(key)) +
      "</span>" +
      '<div class="kv-value">' +
      valueHtml +
      "</div>" +
      (bits.length
        ? '<div class="kv-meta">' + escapeHtml(bits.join(" · ")) + "</div>"
        : "") +
      "</div>"
    );
  }

  function renderExtracted(data) {
    if (!data || typeof data !== "object") {
      return '<p class="hint">No extracted fields.</p>';
    }
    const fields = [];
    Object.keys(data).forEach(function (key) {
      if (key === "line_items" || key === "tables") return;
      fields.push(renderField(key, data[key]));
    });
    return '<div class="kv-grid">' + fields.join("") + "</div>";
  }

  function renderLineItems(items) {
    if (!items || !items.length) {
      return '<p class="hint">No line items extracted.</p>';
    }
    const rows = items
      .map(function (item) {
        return (
          "<tr>" +
          "<td>" +
          escapeHtml(item.description || "—") +
          "</td>" +
          '<td class="num">' +
          escapeHtml(fmt.formatNumber(item.quantity) || "—") +
          "</td>" +
          '<td class="num">' +
          escapeHtml(fmt.formatNumber(item.unit_price) || "—") +
          "</td>" +
          '<td class="num">' +
          escapeHtml(fmt.formatNumber(item.amount) || "—") +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
    return (
      '<div class="table-wrap"><table>' +
      "<thead><tr>" +
      "<th>Description</th><th>Qty</th><th>Unit price</th><th>Amount</th>" +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table></div>"
    );
  }

  function renderTables(tables) {
    if (!tables || !tables.length) {
      return '<p class="hint">No additional tables extracted.</p>';
    }
    return tables.map(function (table) {
      const columns = table.columns || [];
      const head = '<tr><th>Row</th>' + columns.map(function (c) {
        return '<th>' + escapeHtml(c) + '</th>';
      }).join('') + '</tr>';
      const rows = (table.rows || []).map(function (row) {
        const values = row.values || {};
        return '<tr><td>' + escapeHtml(row.label || '—') + '</td>' + columns.map(function (c) {
          return '<td class="num">' + escapeHtml(fmt.formatNumber(values[c]) || '—') + '</td>';
        }).join('') + '</tr>';
      }).join('');
      return '<div style="margin-bottom:18px"><h3>' + escapeHtml(fmt.humanizeKey(table.name || 'table')) + '</h3>' +
        '<div class="table-wrap"><table><thead>' + head + '</thead><tbody>' + rows + '</tbody></table></div></div>';
    }).join('');
  }

  function renderChecks(validation) {
    const checks = (validation && validation.checks) || [];
    if (!checks.length) {
      return '<p class="hint">No validation checks were run.</p>';
    }
    const rows = checks
      .map(function (check) {
        const failed = String(check.status).toUpperCase() === "FAIL";
        return (
          '<tr class="' +
          (failed ? "row-fail" : "") +
          '">' +
          "<td>" +
          escapeHtml(fmt.humanizeKey(check.name)) +
          "</td>" +
          '<td class="num">' +
          escapeHtml(check.formula || "—") +
          "</td>" +
          '<td class="num">' +
          escapeHtml(fmt.formatNumber(check.calculated_value) || "—") +
          "</td>" +
          '<td class="num">' +
          escapeHtml(fmt.formatNumber(check.reported_value) || "—") +
          "</td>" +
          '<td class="num">' +
          escapeHtml(fmt.formatNumber(check.variance) || "—") +
          "</td>" +
          "<td>" +
          statusBadge(check.status) +
          "</td>" +
          "</tr>"
        );
      })
      .join("");

    let issues = "";
    if (validation.issues && validation.issues.length) {
      issues =
        '<ul style="margin:16px 0 0;padding-left:18px;color:var(--fail)">' +
        validation.issues
          .map(function (issue) {
            return "<li>" + escapeHtml(issue) + "</li>";
          })
          .join("") +
        "</ul>";
    }

    return (
      '<div class="table-wrap"><table>' +
      "<thead><tr>" +
      "<th>Check</th><th>Formula</th><th>Calculated</th><th>Reported</th><th>Variance</th><th>Status</th>" +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table></div>" +
      issues
    );
  }

  function renderResult(doc) {
    document.title = doc.document_name + " — Document Intelligence";
    titleEl.textContent = doc.document_name;
    subtitleEl.textContent =
      fmt.documentTypeLabel(doc.document_type) +
      " · overall confidence " +
      (fmt.formatConfidence(doc.overall_confidence) || "n/a");
    statusEl.innerHTML = statusBadge(doc.processing_status);

    const fv = doc.file_validation || {};
    const meta = doc.processing_metadata || {};
    const extracted = doc.extracted_data || {};

    root.innerHTML =
      '<section class="card">' +
      '<div class="meta-strip">' +
      metaItem("File type", fv.file_type || "—") +
      metaItem("Supported", yesNo(fv.is_supported)) +
      metaItem("Readable", yesNo(fv.is_readable)) +
      metaItem("Pages", fv.page_count != null ? String(fv.page_count) : "—") +
      metaItem("File check", fv.status || "—") +
      metaItem("OCR used", yesNo(meta.ocr_used)) +
      metaItem("Processed", fmt.formatDate(meta.processed_at)) +
      metaItem("Duration", fmt.formatDuration(meta.processing_time_ms)) +
      "</div></section>" +
      '<section class="card">' +
      '<div class="section-title"><h2>Extracted fields</h2></div>' +
      renderExtracted(extracted) +
      "</section>" +
      '<section class="card">' +
      '<div class="section-title"><h2>Line items</h2></div>' +
      renderLineItems(extracted.line_items) +
      "</section>" +
      '<section class="card">' +
      '<div class="section-title"><h2>Extracted tables</h2></div>' +
      renderTables(extracted.tables) +
      "</section>" +
      '<section class="card">' +
      '<div class="section-title"><h2>Validation checks</h2>' +
      statusBadge(doc.validation && doc.validation.overall_status) +
      "</div>" +
      renderChecks(doc.validation) +
      "</section>" +
      '<section class="card">' +
      '<div class="section-title">' +
      "<h2>Raw response</h2>" +
      '<button type="button" class="btn btn-ghost" id="toggle-json">View Raw JSON</button>' +
      "</div>" +
      '<pre class="raw-json" id="raw-json">' +
      escapeHtml(JSON.stringify(doc, null, 2)) +
      "</pre>" +
      "</section>";

    const toggle = document.getElementById("toggle-json");
    const pre = document.getElementById("raw-json");
    toggle.addEventListener("click", function () {
      const open = pre.classList.toggle("is-visible");
      toggle.textContent = open ? "Hide Raw JSON" : "View Raw JSON";
    });
  }

  function metaItem(label, value) {
    return (
      '<div class="meta-item"><span>' +
      escapeHtml(label) +
      "</span><strong>" +
      escapeHtml(value) +
      "</strong></div>"
    );
  }

  if (!documentName) {
    titleEl.textContent = "Document not specified";
    root.innerHTML =
      '<div class="alert alert-error is-visible" role="alert">' +
      "Add a document name to the URL, for example " +
      "<code>?name=sample_invoice.pdf</code>. " +
      '<a href="/dashboard.html">Back to dashboard</a>.' +
      "</div>";
    return;
  }

  window.DocIntelAPI.getDocument(documentName)
    .then(renderResult)
    .catch(function (err) {
      titleEl.textContent = documentName;
      root.innerHTML =
        '<div class="alert alert-error is-visible" role="alert">' +
        "<strong>" +
        escapeHtml(err.code || "NOT_FOUND") +
        "</strong>" +
        escapeHtml(err.message || "Document not found.") +
        ' <a href="/dashboard.html">Back to dashboard</a>' +
        "</div>";
    });
})();
