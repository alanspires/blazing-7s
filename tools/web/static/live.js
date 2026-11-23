// Keeps a served page in step with the machine (tools/web_views.py renders every region).
//
// One EventSource on /events streams {"regions": {id: html}} for this page's view: the first
// message is the whole current set, then one message per new machine snapshot. Only regions
// whose markup changed are swapped. A region holding the text field someone is typing in, or
// any field they have edited, is left alone, so a live update never eats input. Sheets and
// panels are URL state and never change under the reader.
//
// While the stream is down every control is off and the server-unreachable banner shows. The
// stream closes while the tab is hidden; on return, controls stay off until the reopened
// stream's first snapshot has been applied. When /events turns the page away (503: too many
// open pages) the page polls GET /regions instead.
//
// Forms posting to /actions/ go by fetch and the page waits for the next snapshot to show the
// result; nothing is ever re-sent. Odds editor edits ask POST /actions/preview for the
// preview regions, debounced.
"use strict";

(function () {
  const POLL_MS = 3000; // GET /regions period when /events is over its subscriber cap
  const REOPEN_MS = 5000; // retry period for a stream the server closed
  const PREVIEW_MS = 400; // quiet time after an odds edit before asking for its preview
  const view = document.body.dataset.view || "page=play";
  const shown = {}; // region id -> markup last applied
  let stream = null;
  let pollTimer = null;
  let reopenTimer = null;
  let previewTimer = null;
  let previewSeq = 0;
  let awaiting = true; // controls stay off until a snapshot has been applied
  let unreachable = false;
  const pendingForms = new Set();

  function parse(markup) {
    const template = document.createElement("template");
    template.innerHTML = markup.trim();
    return template.content.firstElementChild;
  }

  function region(id) {
    return document.querySelector('[data-region="' + CSS.escape(id) + '"]');
  }

  function isTextEntry(element) {
    if (!element) return false;
    if (element.tagName === "TEXTAREA" || element.tagName === "SELECT") return true;
    return element.tagName === "INPUT" && !["button", "submit", "reset", "checkbox", "radio", "hidden"]
      .includes(element.type);
  }

  function edited(field) {
    if (field.type === "checkbox" || field.type === "radio") return field.checked !== field.defaultChecked;
    if (field.tagName === "SELECT") return Array.from(field.options).some((o) => o.selected !== o.defaultSelected);
    return "defaultValue" in field && field.type !== "hidden" && field.value !== field.defaultValue;
  }

  function editorFields() {
    const form = document.getElementById("odds-editor");
    return form ? Array.from(form.elements) : [];
  }

  function editorDirty() {
    return editorFields().some(edited);
  }

  // A region that must not be replaced now: it holds the focused text field or an edited
  // field, or (draft regions) it shows a preview of edits newer than any snapshot.
  function isProtected(element, draftUpdate) {
    const active = document.activeElement;
    if (active && element.contains(active) && isTextEntry(active)) return true;
    if (Array.from(element.querySelectorAll("input, textarea, select")).some(edited)) return true;
    return !draftUpdate && element.hasAttribute("data-draft") && editorDirty();
  }

  function focusKey(element) {
    const active = document.activeElement;
    if (!active || active === document.body || !element.contains(active)) return null;
    return active.dataset.part ? '[data-part="' + CSS.escape(active.dataset.part) + '"]' : null;
  }

  function applyRegions(regions, force, draftUpdate) {
    for (const [id, markup] of Object.entries(regions)) {
      if (!force && shown[id] === markup) continue;
      const element = region(id);
      if (!element || isProtected(element, draftUpdate)) continue;
      const key = focusKey(element);
      const fresh = parse(markup);
      element.replaceWith(fresh);
      shown[id] = markup;
      if (key) {
        const again = fresh.matches(key) ? fresh : fresh.querySelector(key);
        if (again && !again.disabled) again.focus({ preventScroll: true });
      }
    }
  }

  // Every control off, marked so the ones outside the replaced regions come back later.
  function controlsOff() {
    for (const control of document.querySelectorAll("button, input, select, textarea")) {
      if (control.closest("template") || control.disabled) continue;
      control.disabled = true;
      control.dataset.liveOff = "";
    }
    for (const link of document.querySelectorAll("main a[href], .key[href]")) {
      if (link.hasAttribute("aria-disabled")) continue;
      link.setAttribute("aria-disabled", "true");
      link.dataset.liveOff = "";
    }
  }

  function controlsOn() {
    for (const element of document.querySelectorAll("[data-live-off]")) {
      if (element.tagName === "A") element.removeAttribute("aria-disabled");
      else element.disabled = false;
      delete element.dataset.liveOff;
    }
  }

  function snapshot(regions) {
    const recovering = awaiting;
    applyRegions(regions, recovering, false);
    if (editorDirty()) schedulePreview(); // the machine's profile may have moved under the draft
    if (recovering) {
      awaiting = false;
      unreachable = false;
      controlsOn();
    }
  }

  function showUnreachable() {
    if (unreachable) return;
    unreachable = true;
    awaiting = true;
    controlsOff();
    const template = document.getElementById("unreachable");
    if (!template) return;
    for (const replacement of template.content.children) {
      const id = replacement.dataset.region;
      const element = region(id);
      if (!element) continue;
      element.replaceWith(replacement.cloneNode(true));
      delete shown[id];
    }
  }

  function openStream() {
    clearTimeout(reopenTimer);
    stream = new EventSource("/events?" + view);
    stream.onmessage = (event) => snapshot(JSON.parse(event.data).regions);
    stream.onerror = () => {
      if (document.hidden || !stream) return;
      if (stream.readyState !== EventSource.CLOSED) {
        showUnreachable(); // the browser reconnects; its first message restores the page
        return;
      }
      stream = null;
      fetchRegions().then((ok) => {
        if (ok) startPolling();
        else {
          showUnreachable();
          reopenTimer = setTimeout(openStream, REOPEN_MS);
        }
      });
    };
  }

  function closeStream() {
    if (stream) stream.close();
    stream = null;
    clearTimeout(reopenTimer);
    clearTimeout(pollTimer);
    pollTimer = null;
  }

  function fetchRegions() {
    return fetch("/regions?" + view, { headers: { Accept: "application/json" } })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((body) => {
        snapshot(body.regions);
        return true;
      })
      .catch(() => false);
  }

  function startPolling() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(function poll() {
      fetchRegions().then((ok) => {
        if (!ok) showUnreachable();
        if (pollTimer !== null) pollTimer = setTimeout(poll, POLL_MS);
      });
    }, POLL_MS);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      closeStream();
      return;
    }
    awaiting = true;
    controlsOff();
    openStream();
  });

  function flash(text) {
    const box = document.querySelector("[data-flash]");
    if (!box) return;
    box.textContent = text;
    box.hidden = false;
  }

  document.addEventListener("click", (event) => {
    const box = event.target.closest("[data-flash]");
    if (box) box.hidden = true;
    const link = event.target.closest("a[data-live-off]");
    if (link) event.preventDefault();
    const chip = event.target.closest("[data-amount]");
    if (chip) setAmount(chip.closest("form"), Number(chip.dataset.amount), chip);
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    const action = new URL(form.action, location.href);
    if (form.method.toLowerCase() !== "post" || !action.pathname.startsWith("/actions/")) return;
    event.preventDefault();
    if (awaiting || pendingForms.has(form)) return;
    const body = new URLSearchParams(new FormData(form));
    const submitter = event.submitter;
    if (submitter && submitter.name) body.append(submitter.name, submitter.value);
    pendingForms.add(form);
    fetch(action, { method: "POST", body, headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then((response) => {
        if (response.status === 202) return null;
        return response.json().catch(() => ({})).then((answer) => {
          const here = location.pathname + location.search;
          if (answer.location && answer.location !== here) location.assign(answer.location);
          else flash(answer.reason || "Not sent (" + response.status + ").");
        });
      })
      .catch(() => flash("No answer from the server. Nothing is sent again: the page shows what happened once it hears from the server."))
      .finally(() => pendingForms.delete(form));
  });

  // ---------- Add credits ----------

  function setAmount(form, amount, chip) {
    if (!form) return;
    const input = form.querySelector('[name="amount"]');
    input.value = amount.toLocaleString("en-US");
    for (const other of form.querySelectorAll(".chip")) other.classList.toggle("is-on", other === chip);
    amountChanged(form);
  }

  function amountChanged(form) {
    const input = form.querySelector('[name="amount"]');
    const submit = form.querySelector('[type="submit"]');
    const text = Array.from(submit.childNodes).find((node) => node.nodeType === Node.TEXT_NODE);
    if (text) text.textContent = "Add " + input.value + " credits";
  }

  document.addEventListener("input", (event) => {
    const field = event.target;
    if (field.name === "amount" && field.form) amountChanged(field.form);
    if (field.form && field.form.id === "odds-editor") {
      markEdit(field);
      schedulePreview();
    }
  });

  // ---------- Odds editor ----------

  function markEdit(field) {
    const cell = field.closest("td");
    if (!cell || !("base" in field.dataset)) return;
    if (!("serverChanged" in cell.dataset)) cell.dataset.serverChanged = cell.classList.contains("is-changed") ? "1" : "";
    const changed = field.value.replace(/,/g, "").trim() !== field.dataset.base;
    cell.classList.toggle("is-changed", changed || cell.dataset.serverChanged === "1");
    const holder = field.closest(".weight");
    if (holder) {
      let was = holder.querySelector(".weight__was[data-live]");
      if (changed && !was) {
        was = document.createElement("span");
        was.className = "weight__was";
        was.dataset.live = "";
        was.textContent = "was " + field.dataset.base;
        for (const old of holder.querySelectorAll(".weight__was:not([data-live])")) old.hidden = true;
        holder.append(was);
      } else if (!changed && was) {
        was.remove();
        for (const old of holder.querySelectorAll(".weight__was")) old.hidden = false;
      }
    }
    if ("reel" in field.dataset) {
      const total = document.querySelector('[data-total-reel="' + CSS.escape(field.dataset.reel) + '"]');
      const inputs = document.querySelectorAll('.weight__input[data-reel="' + CSS.escape(field.dataset.reel) + '"]');
      const sum = Array.from(inputs).reduce((acc, input) => acc + Number(input.value.replace(/,/g, "").trim() || NaN), 0);
      if (total && total.firstChild) total.firstChild.textContent = Number.isFinite(sum) ? String(sum) : "—";
    }
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(requestPreview, PREVIEW_MS);
  }

  function requestPreview() {
    const form = document.getElementById("odds-editor");
    if (!form) return;
    const seq = ++previewSeq;
    fetch("/actions/preview", {
      method: "POST",
      body: new URLSearchParams(new FormData(form)),
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((answer) => {
        if (seq === previewSeq && !awaiting) applyRegions(answer.regions, true, true);
      })
      .catch(() => flash("The preview could not be worked out. Nothing was sent to the machine."));
  }

  openStream();
})();
