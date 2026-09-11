/**
 * upload.js — Upload page behaviour
 *
 * What this file does, step by step:
 *  1. Listen for a file being chosen (or dropped) and show its name.
 *  2. When Process is clicked, validate the file in the browser.
 *  3. Call DocIntelAPI.processDocument() — today that's a mock delay,
 *     later it will POST multipart/form-data to /api/v1/documents/process.
 *  4. Show a success or error banner using the API error shape.
 */
(function () {
  "use strict";

  const form = document.getElementById("upload-form");
  const typeSelect = document.getElementById("document-type");
  const fileInput = document.getElementById("file-input");
  const fileDrop = document.querySelector(".file-drop");
  const fileName = document.getElementById("file-name");
  const processBtn = document.getElementById("process-btn");
  const errorAlert = document.getElementById("error-alert");
  const successAlert = document.getElementById("success-alert");

  function hideAlerts() {
    errorAlert.classList.remove("is-visible");
    successAlert.classList.remove("is-visible");
    errorAlert.textContent = "";
    successAlert.textContent = "";
  }

  function showError(payload) {
    hideAlerts();
    const err = payload && payload.error ? payload.error : payload;
    const code = err && err.code ? err.code : "ERROR";
    const message =
      err && err.message ? err.message : "Something went wrong. Please try again.";
    errorAlert.innerHTML =
      "<strong>" +
      escapeHtml(code) +
      "</strong>" +
      escapeHtml(message);
    errorAlert.classList.add("is-visible");
  }

  function showSuccess(doc) {
    hideAlerts();
    const href = window.DocIntelFormat.resultHref(doc.document_name);
    successAlert.innerHTML =
      "<strong>Processed</strong>" +
      escapeHtml(doc.document_name) +
      " was accepted. " +
      '<a href="/dashboard.html">View dashboard</a>' +
      " · " +
      '<a href="' +
      href +
      '">Open result</a>';
    successAlert.classList.add("is-visible");
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "\u0026amp;")
      .replace(/</g, "\u0026lt;")
      .replace(/>/g, "\u0026gt;")
      .replace(/"/g, "\u0026#34;");
  }

  function updateFileLabel() {
    const file = fileInput.files && fileInput.files[0];
    fileName.textContent = file ? file.name : "";
  }

  fileInput.addEventListener("change", function () {
    hideAlerts();
    updateFileLabel();
  });

  // Drag-and-drop is extra UX on top of the native file input.
  ["dragenter", "dragover"].forEach(function (eventName) {
    fileDrop.addEventListener(eventName, function (event) {
      event.preventDefault();
      fileDrop.classList.add("is-dragover");
    });
  });
  ["dragleave", "drop"].forEach(function (eventName) {
    fileDrop.addEventListener(eventName, function (event) {
      event.preventDefault();
      fileDrop.classList.remove("is-dragover");
    });
  });
  fileDrop.addEventListener("drop", function (event) {
    const files = event.dataTransfer && event.dataTransfer.files;
    if (files && files[0]) {
      fileInput.files = files;
      updateFileLabel();
    }
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault(); // don't navigate / reload the page
    hideAlerts();

    const file = fileInput.files && fileInput.files[0];
    const documentType = typeSelect.value;

    processBtn.disabled = true;
    processBtn.innerHTML = '<span class="spinner" aria-hidden="true"></span> Processing';

    window.DocIntelAPI.processDocument(file, documentType)
      .then(function (doc) {
        showSuccess(doc);
      })
      .catch(function (err) {
        showError(err);
      })
      .then(function () {
        processBtn.disabled = false;
        processBtn.textContent = "Process";
      });
  });
})();
