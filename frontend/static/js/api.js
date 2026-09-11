/**
 * api.js
 * ------
 * Real backend client. This is a drop-in replacement for the original
 * mock-data.js from the app-builder scaffold: it exposes the exact same
 * `window.DocIntelAPI` and `window.DocIntelFormat` surface, so upload.js,
 * dashboard.js and result.js did not need to change at all.
 *
 * Every function here does a real fetch() to the FastAPI backend - there
 * is no seed data, no localStorage, and no hardcoded results. If the
 * backend returns an error, we throw the exact
 *   { error: { code, message } }
 * object the API sent, so the pages' existing error-rendering code
 * (written for that shape already) keeps working unchanged.
 */

(function () {
  "use strict";

  // Same-origin by default. If the frontend is ever deployed separately
  // from the backend, set window.DOCINT_API_BASE before this script runs
  // (e.g. in a small inline <script> in the HTML) to something like
  // "https://your-backend.onrender.com/api/v1".
  const API_BASE = window.DOCINT_API_BASE || "/api/v1";

  const MAX_FILE_BYTES = 15 * 1024 * 1024; // keep in sync with MAX_UPLOAD_SIZE_MB in backend config
  const ALLOWED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png"];
  const LOW_CONFIDENCE = 0.7;

  async function parseJsonSafely(response) {
    try {
      return await response.json();
    } catch (err) {
      return null;
    }
  }

  function networkError(err) {
    return {
      error: {
        code: "NETWORK_ERROR",
        message:
          "Could not reach the backend API. It may be starting up or temporarily unavailable.",
      },
    };
  }

  /**
   * GET /api/v1/documents
   */
  async function getDocuments() {
    let response;
    try {
      response = await fetch(API_BASE + "/documents");
    } catch (err) {
      throw networkError(err);
    }
    const body = await parseJsonSafely(response);
    if (!response.ok) {
      throw body || { error: { code: "UNKNOWN_ERROR", message: "Failed to load documents." } };
    }
    return body;
  }

  /**
   * GET /api/v1/documents/{document_name}
   */
  async function getDocument(documentName) {
    let response;
    try {
      response = await fetch(API_BASE + "/documents/" + encodeURIComponent(documentName));
    } catch (err) {
      throw networkError(err);
    }
    const body = await parseJsonSafely(response);
    if (!response.ok) {
      throw body || { error: { code: "UNKNOWN_ERROR", message: "Document not found." } };
    }
    return body;
  }

  /**
   * POST /api/v1/documents/process   (multipart: file, document_type)
   */
  async function processDocument(file, documentType) {
    if (!file) {
      throw { error: { code: "MISSING_FILE", message: "Please choose a file to process." } };
    }

    const form = new FormData();
    form.append("file", file);
    form.append("document_type", documentType);

    let response;
    try {
      response = await fetch(API_BASE + "/documents/process", {
        method: "POST",
        body: form,
      });
    } catch (err) {
      throw networkError(err);
    }

    const body = await parseJsonSafely(response);
    if (!response.ok) {
      throw body || { error: { code: "UNKNOWN_ERROR", message: "Processing failed." } };
    }
    return body;
  }

  // ---- Display helpers shared by the pages (unchanged from original) -----

  const TYPE_LABELS = {
    invoice: "Invoice",
    balance_sheet: "Balance sheet",
    profit_and_loss: "Profit & loss",
    cash_flow_statement: "Cash flow statement",
  };

  function documentTypeLabel(type) {
    return TYPE_LABELS[type] || type;
  }

  function humanizeKey(key) {
    return String(key)
      .replace(/_/g, " ")
      .replace(/\b\w/g, function (ch) {
        return ch.toUpperCase();
      });
  }

  function formatDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "number") {
      return value.toLocaleString(undefined, {
        minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
        maximumFractionDigits: 2,
      });
    }
    return String(value);
  }

  function formatConfidence(value) {
    if (value === null || value === undefined) return null;
    return Math.round(value * 100) + "%";
  }

  function formatDuration(ms) {
    if (typeof ms !== "number") return "—";
    if (ms < 1000) return ms + " ms";
    return (ms / 1000).toFixed(1) + " s";
  }

  function isLowConfidence(value) {
    return typeof value === "number" && value < LOW_CONFIDENCE;
  }

  function resultHref(documentName) {
    return "/document_result.html?name=" + encodeURIComponent(documentName);
  }

  window.DocIntelAPI = {
    getDocuments: getDocuments,
    getDocument: getDocument,
    processDocument: processDocument,
    MAX_FILE_BYTES: MAX_FILE_BYTES,
    ALLOWED_EXTENSIONS: ALLOWED_EXTENSIONS,
    LOW_CONFIDENCE: LOW_CONFIDENCE,
  };

  window.DocIntelFormat = {
    documentTypeLabel: documentTypeLabel,
    humanizeKey: humanizeKey,
    formatDate: formatDate,
    formatNumber: formatNumber,
    formatConfidence: formatConfidence,
    formatDuration: formatDuration,
    isLowConfidence: isLowConfidence,
    resultHref: resultHref,
  };
})();
