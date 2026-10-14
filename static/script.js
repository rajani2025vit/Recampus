/* ==========================================================
   ReCampus - shared JavaScript (vanilla, no libraries)
   Sections:
   1. Toast notifications
   2. Mobile navigation
   3. Active navigation link
   4. Smooth scrolling for internal links
   5. Form validation helpers
   6. Image preview
   7. Talking to the Flask server (session, CSRF, api())
   8. Small helpers to build cards safely
   9. Card renderers (Lost, Found, Lend, Borrow requests)
   10. Generic listing (loading / empty / error states)
   11. Navbar sign-in state + sign-in note on forms
   12. Report / listing forms (Lost, Found, Lend)
   13. Sign In and Sign Up forms
   14. Lost, Found, Lend pages
   15. Borrow page (search, filter, request dialog, requests)
   16. Account page
   17. OS Scheduler dashboard
   ========================================================== */

document.addEventListener("DOMContentLoaded", () => {
  setupMobileNav();
  highlightActiveLink();
  setupSmoothScroll();
  setupImagePreviews();
  setupMaxTodayDates();

  // Everything below needs to know who is signed in first.
  App.ready = loadSession().then(() => {
    setupNavAuth();
    setupAuthNotes();
    setupReportForms();
    setupSignInForm();
    setupSignUpForm();
    setupLostPage();
    setupFoundPage();
    setupLendPage();
    setupBorrowPage();
    setupAccountPage();
    setupSchedulerPage();
  });
});

/* 1. Toast notifications ---------------------------------- */
function showToast(message, type = "success") {
  let container = document.querySelector(".toast-container");
  if (!container) {
    container = document.createElement("div");
    container.className = "toast-container";
    container.setAttribute("role", "status");
    container.setAttribute("aria-live", "polite");
    document.body.appendChild(container);
  }

  const toast = document.createElement("div");
  toast.className = "toast" + (type === "error" ? " error" : "");
  toast.textContent = message;
  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add("hide");
    setTimeout(() => toast.remove(), 350);
  }, 4200);
}

/* 2. Mobile navigation ------------------------------------ */
function setupMobileNav() {
  const header = document.querySelector(".site-header");
  const toggle = document.querySelector(".nav-toggle");
  if (!header || !toggle) return;

  toggle.addEventListener("click", () => {
    const isOpen = header.classList.toggle("menu-open");
    toggle.setAttribute("aria-expanded", String(isOpen));
  });

  // Close the menu when a link is chosen or Escape is pressed
  header.addEventListener("click", (event) => {
    if (event.target.closest(".nav-links a, .nav-auth a")) {
      header.classList.remove("menu-open");
      toggle.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && header.classList.contains("menu-open")) {
      header.classList.remove("menu-open");
      toggle.setAttribute("aria-expanded", "false");
      toggle.focus();
    }
  });
}

/* 3. Active navigation link ------------------------------- */
function highlightActiveLink() {
  // Pages can also set <body data-page="lost">; fall back to the file name.
  const bodyPage = document.body.dataset.page;
  const fileName = window.location.pathname.split("/").pop() || "index.html";
  const current = bodyPage || fileName.replace(".html", "") || "index";

  document.querySelectorAll(".nav-links a, .nav-auth a").forEach((link) => {
    const target = link.dataset.page;
    const isActive = target === current || (current === "" && target === "index");
    if (isActive) {
      link.classList.add("active");
      link.setAttribute("aria-current", "page");
    }
  });
}

/* 4. Smooth scrolling for internal links ------------------ */
function setupSmoothScroll() {
  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener("click", (event) => {
      const id = link.getAttribute("href").slice(1);
      const target = id ? document.getElementById(id) : null;
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      target.setAttribute("tabindex", "-1");
      target.focus({ preventScroll: true });
    });
  });
}

/* 5. Form validation helpers ------------------------------ */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function setFieldError(field, message) {
  const group = field.closest(".form-group");
  if (!group) return;
  group.classList.add("has-error");
  field.setAttribute("aria-invalid", "true");
  const errorEl = group.querySelector(".field-error");
  if (errorEl) errorEl.textContent = message;
}

function clearFieldError(field) {
  const group = field.closest(".form-group");
  if (!group) return;
  group.classList.remove("has-error");
  field.removeAttribute("aria-invalid");
}

function showFormMessage(form, message, type) {
  const box = form.querySelector(".form-message");
  if (!box) return;
  box.className = "form-message " + type;
  box.textContent = message;
}

function clearFormMessage(form) {
  const box = form.querySelector(".form-message");
  if (box) {
    box.className = "form-message";
    box.textContent = "";
  }
}

// Checks required fields and email format. Returns true when everything is valid.
function validateForm(form) {
  let isValid = true;
  let firstInvalid = null;

  form.querySelectorAll("input, select, textarea").forEach((field) => {
    clearFieldError(field);
    if (field.type === "file") return;

    const value = field.value.trim();

    if (field.required && value === "") {
      setFieldError(field, "This field is required.");
      isValid = false;
      firstInvalid = firstInvalid || field;
      return;
    }

    if (field.type === "email" && value !== "" && !EMAIL_PATTERN.test(value)) {
      setFieldError(field, "Please enter a valid email address.");
      isValid = false;
      firstInvalid = firstInvalid || field;
    }

    if (field.type === "number" && value !== "" && (Number(value) < 0 || Number.isNaN(Number(value)))) {
      setFieldError(field, "Please enter a number that is 0 or more.");
      isValid = false;
      firstInvalid = firstInvalid || field;
    }

    if (field.type === "date" && value !== "" && field.max && value > field.max) {
      setFieldError(field, "The date cannot be in the future.");
      isValid = false;
      firstInvalid = firstInvalid || field;
    }
  });

  // Image size check (the server checks again)
  form.querySelectorAll('input[type="file"]').forEach((input) => {
    const file = input.files && input.files[0];
    if (file && file.size > 5 * 1024 * 1024) {
      setFieldError(input, "This image is larger than 5 MB. Please choose a smaller one.");
      isValid = false;
      firstInvalid = firstInvalid || input;
    }
  });

  if (firstInvalid) firstInvalid.focus();
  return isValid;
}

// Remove an error as soon as the user edits the field.
function enableLiveErrorClearing(form) {
  form.addEventListener("input", (event) => {
    if (event.target.matches("input, select, textarea")) {
      clearFieldError(event.target);
    }
  });
}

// Date pickers cannot choose a day in the future.
function setupMaxTodayDates() {
  const now = new Date();
  const today = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0") + "-" + String(now.getDate()).padStart(2, "0");
  document.querySelectorAll("input[data-max-today]").forEach((input) => {
    input.max = today;
  });
}

/* 6. Image preview ---------------------------------------- */
function setupImagePreviews() {
  document.querySelectorAll('input[type="file"][data-preview]').forEach((input) => {
    const preview = document.getElementById(input.dataset.preview);
    if (!preview) return;

    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      const group = input.closest(".form-group");
      if (group) group.classList.remove("has-error");

      if (!file) {
        preview.classList.remove("visible");
        preview.removeAttribute("src");
        return;
      }

      if (!file.type.startsWith("image/")) {
        input.value = "";
        preview.classList.remove("visible");
        if (group) {
          group.classList.add("has-error");
          const errorEl = group.querySelector(".field-error");
          if (errorEl) errorEl.textContent = "Please choose an image file (JPG, PNG, etc.).";
        }
        return;
      }

      const reader = new FileReader();
      reader.addEventListener("load", () => {
        preview.src = reader.result;
        preview.classList.add("visible");
      });
      reader.readAsDataURL(file);
    });
  });
}

function resetImagePreviews(form) {
  form.querySelectorAll(".image-preview").forEach((img) => {
    img.classList.remove("visible");
    img.removeAttribute("src");
  });
}

/* 7. Talking to the Flask server -------------------------- */
const App = { user: null, csrf: null, ready: Promise.resolve() };

class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

// Every call to the server goes through this one function.
async function api(url, options = {}) {
  const method = options.method || "GET";
  const init = { method, headers: {}, credentials: "same-origin" };
  if (method !== "GET" && App.csrf) init.headers["X-CSRF-Token"] = App.csrf;
  if (options.json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.json);
  } else if (options.body) {
    init.body = options.body; // FormData: the browser sets the multipart header itself
  }

  let response;
  try {
    response = await fetch(url, init);
  } catch (networkError) {
    throw new ApiError("Cannot reach the ReCampus server. Is it still running (python app.py)?", 0);
  }

  let data = null;
  try {
    data = await response.json();
  } catch (parseError) {
    data = null;
  }

  if (!response.ok) {
    throw new ApiError((data && data.error) || "The server replied with an error (" + response.status + ").", response.status, data);
  }
  return data;
}

async function loadSession() {
  try {
    const data = await api("/api/me");
    App.user = data.user;
    App.csrf = data.csrf;
  } catch (error) {
    App.user = null;
    showToast(error.message, "error");
  }
}

function isAdmin() {
  return !!App.user && App.user.role === "admin";
}

/* 8. Small helpers to build cards safely ------------------ */
// el("div", "item-card", "text") builds an element. Text is inserted with
// textContent, so anything a student types can never run as HTML or script.
function el(tag, className, text, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  if (attrs) Object.keys(attrs).forEach((key) => node.setAttribute(key, attrs[key]));
  return node;
}

function append(parent, ...children) {
  children.forEach((child) => child && parent.appendChild(child));
  return parent;
}

function statusBadge(status) {
  return el("span", "status " + String(status).toLowerCase(), status);
}

function formatDateTime(isoText) {
  if (!isoText) return "";
  const date = new Date(isoText);
  if (Number.isNaN(date.getTime())) return isoText;
  return date.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function formatDay(dayText) {
  const parts = String(dayText || "").split("-");
  if (parts.length !== 3) return dayText || "";
  const date = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  return date.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function timeSince(isoText) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(isoText).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return minutes + " min";
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return hours + " h";
  return Math.floor(hours / 24) + " days";
}

function pidLabel(id) {
  return "PID-" + String(id).padStart(3, "0");
}

const THUMB_ICONS = {
  books: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5z"/><path d="M4 5.5v16"/></svg>',
  electronics: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 11h2M12 11h2M16 11h0M8 15h2M12 15h2M16 15h0"/></svg>',
  lab: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3"/></svg>',
  other: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 8l-9-5-9 5 9 5z"/><path d="M3 8v8l9 5 9-5V8"/></svg>',
};

function thumbKindFor(category) {
  if (category === "Books and notes" || category === "Stationery") return "books";
  if (category === "Electronics") return "electronics";
  if (category === "Lab equipment") return "lab";
  return "other";
}

// Shows the uploaded image, or the same icon thumbnail the original design used.
function buildThumb(imageUrl, category, altText) {
  if (imageUrl) {
    const box = el("div", "item-thumb has-image");
    const img = el("img", null, null, { src: imageUrl, alt: altText, loading: "lazy" });
    box.appendChild(img);
    return box;
  }
  const kind = thumbKindFor(category);
  const box = el("div", "item-thumb thumb-" + kind);
  box.innerHTML = THUMB_ICONS[kind]; // fixed icon markup, contains no user text
  return box;
}

function detailRow(label, value, isPrivate) {
  const row = el("div");
  row.appendChild(el("dt", null, label));
  row.appendChild(el("dd", isPrivate ? "private" : null, value));
  return row;
}

function actionButton(label, className, handler) {
  const button = el("button", "btn btn-small " + className, label, { type: "button" });
  button.addEventListener("click", () => handler(button));
  return button;
}

// Runs one server action (POST) from a button: disables it, shows the result, then refreshes.
async function runAction(button, url, options) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Please wait…";
  try {
    const data = await api(url, Object.assign({ method: "POST" }, options || {}));
    showToast(data.message || "Done.");
    document.dispatchEvent(new CustomEvent("recampus:data-changed"));
  } catch (error) {
    showToast(error.message, "error");
    // The data may have changed under us (for example someone else got there first): show the truth.
    if (error.status === 409 || error.status === 404) {
      document.dispatchEvent(new CustomEvent("recampus:data-changed"));
    }
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

/* 9. Card renderers --------------------------------------- */
function contactRow(contact, hint) {
  if (contact) return detailRow("Contact", contact);
  return detailRow("Contact", hint || "Sign in to see the contact", true);
}

function renderLostCard(report, ctx) {
  const card = el("article", "item-card" + (report.is_mine ? " mine" : "") + (report.status === "Processing" ? " is-running" : ""));
  card.dataset.category = report.category;
  card.dataset.status = report.status;
  card.appendChild(buildThumb(report.image_url, report.category, "Photo of " + report.item_name));

  const meta = el("div", "item-meta");
  meta.appendChild(el("span", "item-category", report.category));
  const badges = el("div", "badge-row");
  append(badges, el("span", "status " + report.priority.toLowerCase(), report.priority + " priority"), statusBadge(report.status));
  meta.appendChild(badges);
  card.appendChild(meta);

  card.appendChild(el("h3", null, report.item_name));
  card.appendChild(el("p", "item-desc", report.description));

  const details = el("dl", "item-details");
  append(details,
    detailRow("Process ID", pidLabel(report.pid)),
    detailRow("Last seen", report.location),
    detailRow("Date lost", formatDay(report.date_lost)),
    detailRow("Reported by", report.reporter + (report.is_mine ? " (you)" : "")),
    detailRow("Submitted", formatDateTime(report.created_at)),
    contactRow(report.contact));
  if (report.status === "Pending" && report.queue_position) {
    details.appendChild(detailRow("Queue", "Position " + report.queue_position + " in the ready queue"));
  }
  if (report.status === "Processing") details.appendChild(detailRow("Started", formatDateTime(report.started_at)));
  if (report.status === "Completed") details.appendChild(detailRow("Completed", formatDateTime(report.completed_at)));
  card.appendChild(details);

  if (report.can_complete) {
    const actions = el("div", "card-actions");
    actions.appendChild(actionButton("Mark Completed", "btn-primary", (button) =>
      runAction(button, "/api/lost/" + report.id + "/complete")));
    card.appendChild(actions);
  }
  return card;
}

function renderFoundCard(report, ctx) {
  const card = el("article", "item-card" + (report.is_mine ? " mine" : ""));
  card.dataset.category = report.category;
  card.dataset.status = report.status;
  card.appendChild(buildThumb(report.image_url, report.category, "Photo of " + report.item_name));

  const meta = el("div", "item-meta");
  meta.appendChild(el("span", "item-category", report.category));
  meta.appendChild(statusBadge(report.status));
  card.appendChild(meta);

  card.appendChild(el("h3", null, report.item_name));
  card.appendChild(el("p", "item-desc", report.description));

  const details = el("dl", "item-details");
  append(details,
    detailRow("Found at", report.location),
    detailRow("Date found", formatDay(report.date_found)),
    detailRow("Found by", report.reporter + (report.is_mine ? " (you)" : "")),
    detailRow("Reported", formatDateTime(report.created_at)),
    contactRow(report.contact));
  if (report.status === "Completed") details.appendChild(detailRow("Completed", formatDateTime(report.completed_at)));
  card.appendChild(details);

  if (report.can_complete) {
    const actions = el("div", "card-actions");
    actions.appendChild(actionButton("Mark Completed", "btn-primary", (button) =>
      runAction(button, "/api/found/" + report.id + "/complete")));
    card.appendChild(actions);
  }
  return card;
}

function priceText(price) {
  if (price === null || price === undefined) return "Free";
  return "₹" + Number(price).toLocaleString("en-IN");
}

// mode "lend": the Lend page list. mode "borrow": the Borrow page (adds the Request button).
function renderLendCard(item, ctx) {
  const card = el("article", "item-card" + (item.is_mine ? " mine" : ""));
  card.dataset.category = item.category;
  card.dataset.status = item.status;
  card.dataset.search = (item.item_name + " " + item.description).toLowerCase();
  card.appendChild(buildThumb(item.image_url, item.category, "Photo of " + item.item_name));

  const meta = el("div", "item-meta");
  meta.appendChild(el("span", "item-category", item.category));
  meta.appendChild(statusBadge(item.status));
  card.appendChild(meta);

  card.appendChild(el("h3", null, item.item_name));
  card.appendChild(el("p", "item-desc", item.description));

  const details = el("dl", "item-details");
  append(details,
    detailRow("Price", priceText(item.price)),
    detailRow("Availability", item.availability),
    detailRow("Lend for", item.duration),
    detailRow("Listed by", item.lender + (item.is_mine ? " (you)" : "")));
  if (item.contact) details.appendChild(detailRow("Contact", item.contact));
  else details.appendChild(detailRow("Contact", "Shared once a request is approved", true));
  if (item.holder_name) details.appendChild(detailRow("Borrowed by", item.holder_name));
  if (item.pending_count) details.appendChild(detailRow("Requests", item.pending_count + " waiting for your decision"));
  card.appendChild(details);

  const actions = el("div", "card-actions");
  if (ctx.mode === "borrow") {
    if (item.is_mine) {
      actions.appendChild(el("span", "muted", "This is your item."));
    } else if (item.my_request_status === "Approved") {
      const done = el("button", "btn btn-secondary btn-small", "Approved: you have this item", { type: "button" });
      done.disabled = true;
      actions.appendChild(done);
    } else if (item.my_request_status === "Pending") {
      const done = el("button", "btn btn-secondary btn-small", "Request pending", { type: "button" });
      done.disabled = true;
      actions.appendChild(done);
    } else if (item.status !== "Available") {
      const done = el("button", "btn btn-secondary btn-small", "Currently borrowed", { type: "button" });
      done.disabled = true;
      actions.appendChild(done);
    } else {
      actions.appendChild(actionButton("Request to Borrow", "btn-primary", () => ctx.onRequest(item)));
    }
  }
  if (item.can_return) {
    actions.appendChild(actionButton("Mark Returned", "btn-primary", (button) =>
      runAction(button, "/api/lend/" + item.id + "/return")));
  }
  if (actions.childNodes.length) card.appendChild(actions);
  return card;
}

// scope "mine": requests I sent. scope "incoming": requests for items I lend.
function renderRequestCard(req, ctx) {
  const card = el("article", "item-card");
  card.dataset.status = req.status;
  card.appendChild(buildThumb(req.item_image_url, req.item_category, "Photo of " + req.item_name));

  const meta = el("div", "item-meta");
  meta.appendChild(el("span", "item-category", "Request #" + req.id));
  meta.appendChild(statusBadge(req.status));
  card.appendChild(meta);
  card.appendChild(el("h3", null, req.item_name));

  const details = el("dl", "item-details");
  details.appendChild(detailRow("Item is", req.item_status));
  if (ctx.scope === "incoming") {
    details.appendChild(detailRow("Requested by", req.borrower));
    details.appendChild(detailRow("Contact", req.borrower_contact || "Hidden", !req.borrower_contact));
  } else {
    details.appendChild(detailRow("Lender", req.lender));
    if (req.status === "Approved") details.appendChild(detailRow("Lender contact", req.lender_contact || ""));
    else details.appendChild(detailRow("Lender contact", "Shown after approval", true));
  }
  if (req.message) details.appendChild(detailRow("Message", req.message));
  details.appendChild(detailRow("Sent", formatDateTime(req.created_at)));
  if (req.decided_at) details.appendChild(detailRow(req.status === "Rejected" ? "Rejected" : "Decided", formatDateTime(req.decided_at)));
  if (req.returned_at) details.appendChild(detailRow("Returned", formatDateTime(req.returned_at)));
  card.appendChild(details);

  const actions = el("div", "card-actions");
  if (req.can_approve) {
    const approve = actionButton("Approve", "btn-primary", (button) =>
      runAction(button, "/api/borrow-requests/" + req.id + "/approve"));
    actions.appendChild(approve);
  }
  if (req.can_reject) {
    actions.appendChild(actionButton("Reject", "btn-danger", (button) =>
      runAction(button, "/api/borrow-requests/" + req.id + "/reject")));
  }
  if (req.can_return) {
    actions.appendChild(actionButton("Mark Returned", "btn-primary", (button) =>
      runAction(button, "/api/lend/" + req.item_id + "/return")));
  }
  if (actions.childNodes.length) card.appendChild(actions);
  return card;
}

/* 10. Generic listing: loading / empty / error states ------ */
// Wires a <section> that contains .loading-state, .error-state, .empty-state and [data-grid].
function createListing(section, config) {
  const grid = section.querySelector("[data-grid]");
  const loading = section.querySelector(".loading-state");
  const errorBox = section.querySelector(".error-state");
  const empty = section.querySelector(".empty-state");
  const countEl = section.querySelector("[data-count]");
  const statusFilter = section.querySelector("[data-status-filter]");
  let items = [];
  let extra = null;
  let loadedOnce = false;
  let busy = false;
  let reloadAfter = false;

  function show(state, message) {
    loading.classList.toggle("visible", state === "loading");
    errorBox.classList.toggle("visible", state === "error");
    empty.classList.toggle("visible", state === "empty");
    grid.hidden = state !== "list";
    if (state === "error") errorBox.querySelector(".error-text").textContent = message || "";
  }

  function visibleItems() {
    return items.filter((item) => {
      if (statusFilter && statusFilter.value !== "all" && item.status !== statusFilter.value) return false;
      return config.filter ? config.filter(item) : true;
    });
  }

  function draw() {
    const list = visibleItems();
    const ctx = Object.assign({ refresh: load }, config.context || {});
    grid.replaceChildren(...list.map((item) => config.render(item, ctx)));
    if (countEl) {
      countEl.textContent = list.length + (list.length === 1 ? " item" : " items") + " shown" +
        (list.length !== items.length ? " (of " + items.length + ")" : "");
    }
    if (list.length === 0) {
      if (config.onEmpty) config.onEmpty(empty, items.length > 0);
      show("empty");
    } else {
      show("list");
    }
    if (config.onData) config.onData(extra, items);
  }

  async function load(options) {
    const silent = options && options.silent;
    if (busy) {
      reloadAfter = true; // data changed while we were loading: load once more afterwards
      return;
    }
    busy = true;
    if (!silent && !loadedOnce) show("loading");
    try {
      const data = await api(config.endpoint);
      items = data.items || [];
      extra = data;
      loadedOnce = true;
      draw();
    } catch (error) {
      if (!loadedOnce) show("error", error.message);
      else if (!silent) showToast(error.message, "error");
    } finally {
      busy = false;
      if (reloadAfter) {
        reloadAfter = false;
        load({ silent: true });
      }
    }
  }

  errorBox.querySelector("[data-retry]").addEventListener("click", () => {
    loadedOnce = false;
    load();
  });
  if (statusFilter) statusFilter.addEventListener("change", draw);
  document.addEventListener("recampus:data-changed", () => load({ silent: true }));

  return { load, draw };
}

// Refreshes a list every few seconds while the tab is visible (so status changes made
// by someone else, for example the administrator, show up without reloading).
function autoRefresh(callback, seconds) {
  setInterval(() => {
    if (document.visibilityState === "visible") callback({ silent: true });
  }, seconds * 1000);
}

/* 11. Navbar sign-in state + sign-in note on forms --------- */
function setupNavAuth() {
  const box = document.getElementById("nav-auth");
  if (!box || !App.user) return;

  box.replaceChildren();
  const who = el("a", "nav-user", App.user.name + (isAdmin() ? " (admin)" : ""), { href: "account.html", title: "My activity", "data-page": "account" });
  const out = el("button", "btn btn-ghost btn-small", "Sign Out", { type: "button" });
  out.addEventListener("click", async () => {
    out.disabled = true;
    try {
      await api("/api/signout", { method: "POST" });
    } catch (error) {
      showToast(error.message, "error");
    }
    window.location.href = "index.html";
  });
  append(box, who, out);
  if (document.body.dataset.page === "account") who.classList.add("active");
}

// The green note above each form says whether you can submit.
function setupAuthNotes() {
  document.querySelectorAll("[data-auth-note]").forEach((note) => {
    const action = note.dataset.authAction || "do this";
    note.replaceChildren();
    if (App.user) {
      append(note, el("strong", null, "Signed in as " + App.user.name + ". "),
        document.createTextNode("Everything you submit is saved permanently in the database and appears below."));
    } else {
      const next = encodeURIComponent(document.body.dataset.page + ".html");
      const link = el("a", null, "Sign in", { href: "signin.html?next=" + next });
      const signup = el("a", null, "create an account", { href: "signup.html" });
      append(note, el("strong", null, "Please sign in to " + action + ". "),
        document.createTextNode("You can still browse everything. "), link, document.createTextNode(" or "), signup, document.createTextNode("."));
    }
  });

  // Hide the sections that only make sense when signed in.
  document.querySelectorAll("[data-auth-only]").forEach((section) => {
    section.hidden = !App.user;
  });
}

/* 12. Report / listing forms (Lost, Found, Lend) ----------- */
// One single submit handler per form: sends the form (with the optional image) to the server.
function setupReportForms() {
  document.querySelectorAll("form[data-report-form]").forEach((form) => {
    enableLiveErrorClearing(form);
    const submitButton = form.querySelector('button[type="submit"]');

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearFormMessage(form);

      if (!validateForm(form)) {
        showFormMessage(form, "Please fix the highlighted fields and try again.", "error");
        showToast("Some fields need your attention.", "error");
        return;
      }
      if (!App.user) {
        showFormMessage(form, "Please sign in first. Use the Sign In button at the top of the page.", "error");
        showToast("Please sign in to submit.", "error");
        return;
      }

      const originalLabel = submitButton.textContent;
      submitButton.disabled = true;
      submitButton.textContent = "Saving…";
      try {
        const data = await api(form.dataset.endpoint, { method: "POST", body: new FormData(form) });
        showFormMessage(form, data.message, "success");
        showToast("Saved to the database.");
        form.reset();
        resetImagePreviews(form);
        document.dispatchEvent(new CustomEvent("recampus:data-changed"));
      } catch (error) {
        showFormMessage(form, error.message, "error");
        showToast(error.message, "error");
      } finally {
        submitButton.disabled = false;
        submitButton.textContent = originalLabel;
      }
    });
  });
}

/* 13. Sign In and Sign Up forms ---------------------------- */
function safeNextPage() {
  const next = new URLSearchParams(window.location.search).get("next") || "";
  return /^(index|lost|found|borrow|lend|scheduler|account)\.html$/.test(next) ? next : "index.html";
}

function setupSignInForm() {
  const form = document.getElementById("signin-form");
  if (!form) return;
  enableLiveErrorClearing(form);

  if (App.user) {
    showFormMessage(form, "You are already signed in as " + App.user.name + ".", "success");
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFormMessage(form);

    if (!validateForm(form)) {
      showFormMessage(form, "Please fix the highlighted fields and try again.", "error");
      return;
    }

    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = "Signing in…";
    try {
      const data = await api("/api/signin", {
        method: "POST",
        json: { email: form.querySelector("#signin-email").value.trim(), password: form.querySelector("#signin-password").value },
      });
      App.csrf = data.csrf;
      showFormMessage(form, "Signed in. Taking you back…", "success");
      setTimeout(() => { window.location.href = safeNextPage(); }, 600);
    } catch (error) {
      showFormMessage(form, error.message, "error");
      button.disabled = false;
      button.textContent = "Sign In";
    }
  });
}

function setupSignUpForm() {
  const form = document.getElementById("signup-form");
  if (!form) return;
  enableLiveErrorClearing(form);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFormMessage(form);

    let isValid = validateForm(form);

    const email = form.querySelector("#signup-email");
    const password = form.querySelector("#signup-password");
    const confirm = form.querySelector("#signup-confirm");

    // Basic rule: student email should be a VIT address
    if (email.value.trim() !== "" && EMAIL_PATTERN.test(email.value.trim())) {
      if (!email.value.trim().toLowerCase().endsWith("@vitstudent.ac.in")) {
        setFieldError(email, "Please use your VIT student email (ending in @vitstudent.ac.in).");
        isValid = false;
      }
    }

    if (password.value !== "" && password.value.length < 8) {
      setFieldError(password, "Password must be at least 8 characters.");
      isValid = false;
    }

    if (confirm.value !== "" && confirm.value !== password.value) {
      setFieldError(confirm, "Passwords do not match.");
      isValid = false;
    }

    if (!isValid) {
      showFormMessage(form, "Please fix the highlighted fields and try again.", "error");
      return;
    }

    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = "Creating account…";
    try {
      const data = await api("/api/signup", {
        method: "POST",
        json: {
          name: form.querySelector("#signup-name").value.trim(),
          email: email.value.trim(),
          password: password.value,
          confirm: confirm.value,
        },
      });
      App.csrf = data.csrf;
      showFormMessage(form, data.message + " Taking you to the home page…", "success");
      setTimeout(() => { window.location.href = safeNextPage(); }, 800);
    } catch (error) {
      showFormMessage(form, error.message, "error");
      button.disabled = false;
      button.textContent = "Sign Up";
    }
  });
}

/* 14. Lost, Found, Lend pages ------------------------------ */
function setupLostPage() {
  const section = document.getElementById("lost-listing");
  if (!section) return;
  const banner = document.getElementById("lost-listing-scheduler");

  const listing = createListing(section, {
    endpoint: "/api/lost",
    render: renderLostCard,
    onEmpty: (box, hasItems) => {
      box.querySelector("strong").textContent = hasItems ? "No reports match this filter." : "No lost items have been reported yet.";
    },
    onData: (data) => renderSchedulerBanner(banner, data),
  });
  listing.load();
  autoRefresh(listing.load, 8);
}

// A small status bar above the Lost list: who is "running", and (for admins) the two scheduler buttons.
function renderSchedulerBanner(banner, data) {
  if (!banner || !data || !data.scheduler) return;
  banner.hidden = false;
  banner.replaceChildren();

  const text = el("div");
  const running = data.scheduler.running;
  text.appendChild(el("strong", null, "OS scheduler: "));
  text.appendChild(document.createTextNode(running
    ? pidLabel(running.id) + " “" + running.item_name + "” is Processing. "
    : "no report is Processing (CPU idle). "));
  text.appendChild(document.createTextNode(data.scheduler.pending_count + " waiting in the Pending queue. "));
  text.appendChild(el("a", null, "Open the OS dashboard", { href: "scheduler.html" }));
  banner.appendChild(text);

  if (data.can_schedule) {
    const actions = el("div", "banner-actions");
    append(actions,
      actionButton("Process Next Report", "btn-primary", (button) => runAction(button, "/api/scheduler/next")),
      actionButton("Mark Completed", "btn-secondary", (button) => runAction(button, "/api/scheduler/complete")));
    banner.appendChild(actions);
  }
}

function setupFoundPage() {
  const section = document.getElementById("found-listing");
  if (!section) return;
  const listing = createListing(section, {
    endpoint: "/api/found",
    render: renderFoundCard,
    onEmpty: (box, hasItems) => {
      box.querySelector("strong").textContent = hasItems ? "No reports match this filter." : "No found items have been reported yet.";
    },
  });
  listing.load();
  autoRefresh(listing.load, 15);
}

function setupLendPage() {
  const section = document.getElementById("lend-listing");
  if (!section) return;
  const listing = createListing(section, {
    endpoint: "/api/lend",
    render: renderLendCard,
    context: { mode: "lend" },
    onEmpty: (box, hasItems) => {
      box.querySelector("strong").textContent = hasItems ? "No items match this filter." : "No items have been listed yet.";
    },
  });
  listing.load();
  autoRefresh(listing.load, 15);
}

/* 15. Borrow page: search, filter, request dialog ---------- */
function setupBorrowPage() {
  const section = document.getElementById("borrow-listing");
  if (!section) return;

  const searchInput = document.getElementById("borrow-search");
  const categorySelect = document.getElementById("borrow-category");
  const dialog = document.getElementById("borrow-dialog");
  const dialogForm = document.getElementById("borrow-dialog-form");
  let pendingItem = null;

  const items = createListing(section, {
    endpoint: "/api/lend",
    render: renderLendCard,
    context: { mode: "borrow", onRequest: openRequestDialog },
    filter: (item) => {
      const query = searchInput.value.trim().toLowerCase();
      const category = categorySelect.value;
      const matchesText = query === "" || (item.item_name + " " + item.description).toLowerCase().includes(query);
      const matchesCategory = category === "all" || item.category === category;
      return matchesText && matchesCategory;
    },
    onEmpty: (box, hasItems) => {
      box.querySelector("strong").textContent = hasItems ? "No items match your search." : "No items have been listed yet.";
      box.querySelector("p:last-child").textContent = hasItems
        ? "Try a different keyword or choose another category."
        : "Items appear here as soon as a student lists them on the Lend page.";
    },
  });
  // The original page's empty-state box (#borrow-empty) is reused as this list's empty state.
  searchInput.addEventListener("input", items.draw);
  categorySelect.addEventListener("change", items.draw);
  items.load();
  autoRefresh(items.load, 15);

  // Sections shown only when signed in
  const mine = document.getElementById("borrow-mine");
  const incoming = document.getElementById("borrow-incoming");
  if (App.user) {
    const mineList = createListing(mine, { endpoint: "/api/borrow-requests?scope=mine", render: renderRequestCard, context: { scope: "mine" } });
    const incomingList = createListing(incoming, { endpoint: "/api/borrow-requests?scope=incoming", render: renderRequestCard, context: { scope: "incoming" } });
    mineList.load();
    incomingList.load();
    autoRefresh(mineList.load, 15);
    autoRefresh(incomingList.load, 15);
  }

  /* Request dialog */
  function openRequestDialog(item) {
    if (!App.user) {
      showToast("Please sign in to request an item.", "error");
      return;
    }
    pendingItem = item;
    document.getElementById("borrow-dialog-item").textContent = item.item_name + " (from " + item.lender + ")";
    document.getElementById("borrow-contact").value = App.user.email;
    document.getElementById("borrow-message").value = "";
    clearFormMessage(dialogForm);
    dialogForm.querySelectorAll(".form-group").forEach((group) => group.classList.remove("has-error"));
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    document.getElementById("borrow-contact").focus();
  }

  document.getElementById("borrow-dialog-cancel").addEventListener("click", () => dialog.close());
  enableLiveErrorClearing(dialogForm);

  dialogForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFormMessage(dialogForm);
    if (!validateForm(dialogForm)) return;

    const send = document.getElementById("borrow-dialog-send");
    send.disabled = true;
    send.textContent = "Sending…";
    try {
      const data = await api("/api/borrow-requests", {
        method: "POST",
        json: {
          item_id: pendingItem.id,
          contact: document.getElementById("borrow-contact").value.trim(),
          message: document.getElementById("borrow-message").value.trim(),
        },
      });
      dialog.close();
      showToast(data.message);
      document.dispatchEvent(new CustomEvent("recampus:data-changed"));
    } catch (error) {
      showFormMessage(dialogForm, error.message, "error");
      if (error.status === 409) document.dispatchEvent(new CustomEvent("recampus:data-changed"));
    } finally {
      send.disabled = false;
      send.textContent = "Send Request";
    }
  });
}

/* 16. Account page ----------------------------------------- */
function setupAccountPage() {
  if (document.body.dataset.page !== "account") return;
  const prompt = document.getElementById("account-signin-prompt");
  if (!App.user) {
    prompt.hidden = false;
    return;
  }
  document.getElementById("account-name").textContent = App.user.name;
  document.getElementById("account-email").textContent = App.user.email;
  const role = document.getElementById("account-role");
  role.textContent = isAdmin() ? "Administrator" : "Student";
  role.classList.add("role");

  const sections = [
    ["account-lost", "/api/lost?mine=1", renderLostCard, {}],
    ["account-found", "/api/found?mine=1", renderFoundCard, {}],
    ["account-lend", "/api/lend?mine=1", renderLendCard, { mode: "lend" }],
    ["account-requests", "/api/borrow-requests?scope=mine", renderRequestCard, { scope: "mine" }],
    ["account-incoming", "/api/borrow-requests?scope=incoming", renderRequestCard, { scope: "incoming" }],
  ];
  sections.forEach(([id, endpoint, render, context]) => {
    const listing = createListing(document.getElementById(id), { endpoint, render, context });
    listing.load();
    autoRefresh(listing.load, 15);
  });
}

/* 17. OS Scheduler dashboard -------------------------------- */
function setupSchedulerPage() {
  const diagram = document.getElementById("state-diagram");
  if (!diagram) return;

  const nextButton = document.getElementById("btn-next");
  const completeButton = document.getElementById("btn-complete");
  const message = document.getElementById("sched-message");
  const roleNote = document.getElementById("sched-role-note");
  let latest = null;

  function setMessage(text, type) {
    message.className = "form-message " + type;
    message.textContent = text;
  }

  function fillTable(tableId, emptyId, rows, buildRow) {
    const table = document.getElementById(tableId);
    const empty = document.getElementById(emptyId);
    const body = table.querySelector("tbody");
    body.replaceChildren(...rows.map(buildRow));
    table.closest(".table-wrap").hidden = rows.length === 0;
    empty.classList.toggle("visible", rows.length === 0);
  }

  function cell(content) {
    const td = document.createElement("td");
    if (content instanceof Node) td.appendChild(content);
    else td.textContent = content;
    return td;
  }

  function rowOf(...cells) {
    const tr = document.createElement("tr");
    cells.forEach((c) => tr.appendChild(c));
    return tr;
  }

  function render(data) {
    latest = data;
    document.getElementById("count-pending").textContent = data.counts.Pending;
    document.getElementById("count-processing").textContent = data.counts.Processing;
    document.getElementById("count-completed").textContent = data.counts.Completed;
    diagram.querySelector(".state-node.processing").classList.toggle("active", !!data.running);

    // The "CPU": what is Processing right now
    const cpu = document.getElementById("cpu-content");
    cpu.replaceChildren();
    if (data.running) {
      const r = data.running;
      append(cpu,
        el("div", "cpu-pid", pidLabel(r.pid) + " · RUNNING"),
        el("div", "cpu-name", r.item_name),
        append(el("div", "badge-row"), el("span", "status " + r.priority.toLowerCase(), r.priority + " priority"), statusBadge(r.status)),
        el("p", "muted", "Started " + formatDateTime(r.started_at) + ". Non-preemptive: nothing can interrupt it until it is completed.", { style: "margin-top: 10px;" }));
    } else {
      cpu.appendChild(el("div", "cpu-idle muted", "CPU idle: no report is Processing. An administrator can press “Process Next Report”."));
    }

    // Buttons and role note
    if (data.can_schedule) {
      roleNote.textContent = "You are an administrator, so you can run the dispatcher.";
    } else if (App.user) {
      roleNote.textContent = "Only administrators can run the dispatcher. You can still watch the queue live. (The owner of the running report can also complete it from the Lost page.)";
    } else {
      roleNote.textContent = "Sign in as an administrator to use these buttons. Anyone can watch the queue.";
    }
    nextButton.disabled = !data.can_schedule;
    completeButton.disabled = !data.can_schedule || !data.running;

    // Ready queue
    fillTable("queue-table", "queue-empty", data.queue, (r, index) => {
      const tr = rowOf(
        cell(index === 0 ? "▶ next" : "#" + r.queue_position),
        cell(pidLabel(r.pid)),
        cell(r.item_name),
        cell(el("span", "status " + r.priority.toLowerCase(), r.priority)),
        cell(statusBadge("Pending")),
        cell(formatDateTime(r.created_at)),
        cell(timeSince(r.created_at)));
      if (index === 0) tr.className = "next-up";
      return tr;
    });

    fillTable("completed-table", "completed-empty", data.completed, (r) => rowOf(
      cell(pidLabel(r.pid)), cell(r.item_name), cell(el("span", "status " + r.priority.toLowerCase(), r.priority)), cell(statusBadge("Completed"))));

    // State-change log
    const logList = document.getElementById("log-list");
    logList.replaceChildren(...data.log.map((entry) => {
      const li = el("li");
      const from = entry.from_state || "New";
      li.appendChild(el("strong", null, pidLabel(entry.report_id) + " "));
      li.appendChild(document.createTextNode(entry.item_name + ": " + from + " → " + entry.to_state));
      li.appendChild(el("span", "log-time", formatDateTime(entry.at) + " · by " + entry.actor));
      return li;
    }));
    document.getElementById("log-empty").classList.toggle("visible", data.log.length === 0);
    logList.hidden = data.log.length === 0;

    // Lend items = exclusive resources
    fillTable("resource-table", "resource-empty", data.resources, (r) => rowOf(
      cell(r.item_name), cell(statusBadge(r.status)), cell(r.status === "Borrowed" ? (r.holder || "a student") : "—"), cell(String(r.waiting))));

    document.getElementById("last-refresh").textContent = "Last update: " + new Date().toLocaleTimeString("en-IN");
  }

  async function load(options) {
    try {
      render(await api("/api/scheduler"));
    } catch (error) {
      if (!(options && options.silent)) setMessage(error.message, "error");
    }
  }

  async function press(button, url) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Working…";
    try {
      const data = await api(url, { method: "POST" });
      setMessage(data.message, "success");
      showToast(data.message);
    } catch (error) {
      setMessage(error.message, "error");
      showToast(error.message, "error");
    } finally {
      button.textContent = original;
      await load();
    }
  }

  nextButton.addEventListener("click", () => press(nextButton, "/api/scheduler/next"));
  completeButton.addEventListener("click", () => press(completeButton, "/api/scheduler/complete"));
  document.addEventListener("recampus:data-changed", () => load({ silent: true }));

  nextButton.disabled = true;
  completeButton.disabled = true;
  load();
  autoRefresh(load, 4);
}
