// Keeps a served page in step with the machine (tools/web_views.py renders every region).
//
// One EventSource on /events streams {"regions": {id: html}} for this page's view: the first
// message is the whole current set, then one message per new machine snapshot. Only regions
// whose markup changed are swapped. A region holding the text field someone is typing in, or
// any field they have edited, is left alone, so a live update never eats input. Sheets and
// panels are URL state and never change under the reader.
//
// While the stream is down every control is off and the server-unreachable banner shows. The
// server sends a heartbeat event whenever the stream is otherwise quiet, naming the deadline
// after which silence means the connection is gone even though the browser saw no close: the
// page then shows the server unreachable and opens a fresh stream. The stream closes while
// the tab is hidden; on return, controls stay off until the reopened stream's first snapshot
// has been applied. When /events turns the page away (503: too many open pages) the page
// polls GET /regions instead.
//
// The Add credits amount is a field like any other, so a typed amount survives live updates;
// whether it can be sent is still the server's call. While it holds a typed amount, the sheet
// is re-rendered for that amount by GET /regions and everything around the field taken from
// it: the submit button's state and reason, the error and the chips.
//
// Forms posting to /actions/ go by fetch and the page waits for the next snapshot to show the
// result; nothing is ever re-sent. The page keeps the submission IDs its 202 answers name:
// when the worker later refuses one of them (the status region lists refused submissions),
// the page flashes that refusal once, with its code, unless a region already shows it.
//
// Odds editor edits ask POST /actions/preview for the preview regions, debounced; the preview
// admits Apply for the generation the draft's form carries, so a draft kept across another
// page's apply shows Apply out of date. Once this page's own apply is verified on the machine
// and its profile is still the active one (the editor region names its submission ID), the
// editor is rebased in place: each field's baseline, the cell marks and the form's generation
// become the applied profile's, while every field node stays, and a focused field or one
// typed in since the apply keeps its value, caret and selection, as an edit of the new
// baseline.
"use strict";

(function () {
  const POLL_MS = 3000; // GET /regions period when /events is over its subscriber cap
  const REOPEN_MS = 5000; // retry period for a stream the server closed
  const PREVIEW_MS = 400; // quiet time after an odds edit before asking for its preview
  const AMOUNT_MS = 400; // quiet time after an amount edit before asking whether it can go
  const view = document.body.dataset.view || "page=play";
  const FIELD = "input[name], select[name], textarea[name]";
  const shown = {}; // region id -> markup last applied
  const received = {}; // region id -> markup last received, applied or not
  let stream = null;
  let pollTimer = null;
  let reopenTimer = null;
  let previewTimer = null;
  let previewSeq = 0;
  let amountTimer = null;
  let amountSeq = 0;
  let silenceTimer = null;
  let silenceMs = null; // the server's deadline for a quiet stream, from its heartbeat
  let awaiting = true; // controls stay off until a snapshot has been applied
  let unreachable = false;
  let ownApply = null; // this page's apply until it is verified: {id, values posted}
  // Submission IDs this page's 202 answers named, oldest first, until refused or aged out of
  // the server's results ring (web_state.RESULTS_LIMIT newest results).
  const OWN_LIMIT = 32;
  const ownSubmissions = new Set();
  const pendingForms = new Set();
  const sendingButtons = new Map(); // button -> submission ID (or null before 202)

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
      if (!element) continue;
      const fresh = parse(markup);
      // Let busy draft regions (Applying… / Restoring…) enter and leave their loading state
      // without waiting for editorDirty() to clear, while leaving the editor inputs untouched.
      const busyChange = element.hasAttribute("data-draft")
        && (fresh.hasAttribute("data-busy") || element.hasAttribute("data-busy"));
      if (!busyChange && isProtected(element, draftUpdate)) continue;
      const key = focusKey(element);
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
    Object.assign(received, regions);
    rebaseEditor();
    applyRegions(regions, recovering, false);
    reconcileSending();
    showOwnRefusals();
    if (editorDirty()) schedulePreview(); // the machine's profile may have moved under the draft
    const credits = region("add-credits");
    if (credits && isProtected(credits, false)) scheduleAmountCheck(); // and admission under the amount
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

  // Restart the silence deadline: called on everything the stream delivers.
  function heard() {
    clearTimeout(silenceTimer);
    if (silenceMs !== null) silenceTimer = setTimeout(silent, silenceMs);
  }

  function silent() {
    showUnreachable();
    if (stream) stream.close();
    openStream(); // its first snapshot restores the page
  }

  function openStream() {
    clearTimeout(reopenTimer);
    stream = new EventSource("/events?" + view);
    heard(); // a connection that never delivers is as lost as one that stops
    stream.onmessage = (event) => {
      heard();
      snapshot(JSON.parse(event.data).regions);
    };
    stream.addEventListener("heartbeat", (event) => {
      silenceMs = JSON.parse(event.data).deadline * 1000;
      heard();
    });
    stream.onerror = () => {
      if (document.hidden || !stream) return;
      if (stream.readyState !== EventSource.CLOSED) {
        showUnreachable(); // the browser reconnects; its first message restores the page
        return;
      }
      clearTimeout(silenceTimer);
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
    clearTimeout(silenceTimer);
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

  function flash(text, code) {
    const box = document.querySelector("[data-flash]");
    if (!box) return;
    box.textContent = text;
    if (code) box.dataset.code = code;
    else delete box.dataset.code;
    box.hidden = false;
  }

  function noteSubmission(id) {
    ownSubmissions.add(id);
    if (ownSubmissions.size > OWN_LIMIT) ownSubmissions.delete(ownSubmissions.values().next().value);
    showOwnRefusals(); // in case its refusal arrived before this answer
  }

  // Flash, once, the worker's refusal of a submission this page made, wherever it sits among
  // newer results; a region that already shows it (the Play banner, the Odds status, a
  // Settings row) is enough.
  function showOwnRefusals() {
    const status = received.status && parse(received.status);
    if (!status || !status.dataset.refused || !ownSubmissions.size) return;
    for (const refused of JSON.parse(status.dataset.refused)) {
      if (!ownSubmissions.delete(refused.id)) continue;
      if (document.querySelector('[data-submission="' + CSS.escape(refused.id) + '"]')) continue;
      flash(refused.text, refused.code);
    }
  }

  document.addEventListener("click", (event) => {
    const box = event.target.closest("[data-flash]");
    if (box) box.hidden = true;
    const link = event.target.closest("a[data-live-off]");
    if (link) event.preventDefault();
    const chip = event.target.closest("[data-amount]");
    if (chip) setAmount(chip.closest("form"), Number(chip.dataset.amount), chip);
  });

  function markSending(button) {
    if (!button) return;
    button.classList.add("is-sending");
    button.setAttribute("aria-busy", "true");
    if (!button.classList.contains("switch") && !button.querySelector(".spinner")) {
      const spinner = document.createElement("span");
      spinner.className = "spinner";
      spinner.setAttribute("aria-hidden", "true");
      button.prepend(spinner);
    }
    sendingButtons.set(button, { id: null, seenPending: false });
  }

  function clearSending(button) {
    if (!button) return;
    button.classList.remove("is-sending");
    button.removeAttribute("aria-busy");
    const spinner = button.querySelector(".spinner");
    if (spinner) spinner.remove();
    sendingButtons.delete(button);
  }

  function reconcileSending() {
    if (!sendingButtons.size) return;
    const status = received.status && parse(received.status);
    const pending = new Set(status && status.dataset.pending ? JSON.parse(status.dataset.pending) : []);
    const settled = new Set(status && status.dataset.settled ? JSON.parse(status.dataset.settled) : []);
    for (const [button, entry] of Array.from(sendingButtons.entries())) {
      if (!button.isConnected) {
        sendingButtons.delete(button);
      } else if (entry.id !== null) {
        if (pending.has(entry.id)) {
          entry.seenPending = true;
        } else if (settled.has(entry.id) || entry.seenPending) {
          clearSending(button);
        }
      }
    }
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    const action = new URL(form.action, location.href);
    if (form.method.toLowerCase() !== "post" || !action.pathname.startsWith("/actions/")) return;
    event.preventDefault();
    const submitter = event.submitter || form.querySelector('[type="submit"]');
    if (awaiting || pendingForms.has(form) || (submitter && sendingButtons.has(submitter))) return;
    const body = new URLSearchParams(new FormData(form));
    if (event.submitter && event.submitter.name) body.append(event.submitter.name, event.submitter.value);
    pendingForms.add(form);
    markSending(submitter);
    const posted = Object.fromEntries(body);
    fetch(action, { method: "POST", body, headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then((response) => {
        if (response.status === 202) {
          return response.json().then((answer) => {
            if (submitter && sendingButtons.has(submitter)) {
              sendingButtons.get(submitter).id = answer.submission_id;
              reconcileSending();
            }
            if (form.id === "odds-editor") {
              ownApply = { id: answer.submission_id, values: posted };
              rebaseEditor(); // in case its verification arrived first
            }
            if (action.pathname === "/actions/set-option") {
              // Unprotect the options region once accepted so the server's Saving… and verified value render.
              for (const field of form.querySelectorAll(FIELD)) {
                if (field.tagName === "SELECT") {
                  for (const opt of field.options) opt.defaultSelected = opt.selected;
                } else if ("defaultValue" in field && field.type !== "hidden") {
                  field.defaultValue = field.value;
                }
              }
              if (document.activeElement && form.contains(document.activeElement)) document.activeElement.blur();
            }
            noteSubmission(answer.submission_id);
          });
        }
        clearSending(submitter);
        return response.json().catch(() => ({})).then((answer) => {
          const here = location.pathname + location.search;
          if (answer.location && answer.location !== here) location.assign(answer.location);
          else flash(answer.reason || "Not sent (" + response.status + ").");
        });
      })
      .catch(() => {
        clearSending(submitter);
        flash("No answer from the server. Nothing is sent again: the page shows what happened once it hears from the server.");
      })
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
    scheduleAmountCheck();
  }

  function scheduleAmountCheck() {
    clearTimeout(amountTimer);
    const seq = ++amountSeq; // an answer for an older amount or snapshot is dropped
    amountTimer = setTimeout(() => checkAmount(seq), AMOUNT_MS);
  }

  function checkAmount(seq) {
    const sheet = region("add-credits");
    const input = sheet && sheet.querySelector('[name="amount"]');
    if (!input) return;
    const query = new URLSearchParams(view);
    query.set("amount", input.value);
    fetch("/regions?" + query, { headers: { Accept: "application/json" } })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((body) => body, () => {
        flash("Could not check this amount with the server. Nothing was sent to the machine.");
        return null;
      })
      .then((body) => {
        const current = region("add-credits");
        if (!body || seq !== amountSeq || awaiting || !current || !current.contains(input)) return;
        // The typed amount keeps its value and its rendered default, so it stays an edit.
        morph(current, parse(body.regions["add-credits"]),
          (field, twin) => copyAttributes(field, twin, field === input ? ["value"] : []));
      });
  }

  // Make element match fresh, its re-rendering, while every named form field inside it stays
  // the same node, so its focus, caret, selection and typed value survive: adopt(field, twin)
  // brings each field in line with its twin instead. Everything else is fresh's markup, and
  // fields come in the same order in both, or the region is not a re-rendering of element.
  function morph(element, fresh, adopt) {
    copyAttributes(element, fresh, []);
    const theirs = Array.from(fresh.children);
    const kept = new Map(); // fresh's child -> element's child holding the same field
    let last = -1;
    for (const child of Array.from(element.children)) {
      const named = child.matches(FIELD) ? child : child.querySelector(FIELD);
      if (!named) continue;
      const twin = fresh.querySelector('[name="' + CSS.escape(named.name) + '"]');
      const at = twin ? theirs.findIndex((other) => other.contains(twin)) : -1;
      if (at <= last || theirs[at].tagName !== child.tagName) {
        throw new Error("the re-rendered region differs in shape around " + named.name);
      }
      last = at;
      kept.set(theirs[at], child);
    }
    const keep = new Set(kept.values());
    for (const node of Array.from(element.childNodes)) if (!keep.has(node)) node.remove();
    let next = element.firstChild;
    for (const node of Array.from(fresh.childNodes)) {
      const mine = kept.get(node);
      if (!mine) {
        element.insertBefore(node, next);
        continue;
      }
      next = mine.nextSibling;
      if (mine.matches(FIELD)) adopt(mine, node);
      else morph(mine, node, adopt);
    }
  }

  function copyAttributes(target, source, skip) {
    for (const { name } of Array.from(target.attributes)) {
      if (!skip.includes(name) && !source.hasAttribute(name)) target.removeAttribute(name);
    }
    for (const { name, value } of Array.from(source.attributes)) {
      if (!skip.includes(name)) target.setAttribute(name, value);
    }
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

  // The editor region as last received names this page's apply as the one that committed the
  // active profile: rebase the editor onto it in place. Each named field keeps its node; it
  // takes its twin's attributes (data-base among them) and default value, and its twin's value
  // too unless it has focus or was typed in since the apply. Everything else in the editor is
  // the server's markup again, then the live change marks are worked out for the new baseline.
  function rebaseEditor() {
    const fresh = ownApply && received.editor && parse(received.editor);
    if (!fresh || fresh.dataset.applied !== ownApply.id) return;
    const posted = ownApply.values;
    ownApply = null;
    const element = region("editor");
    if (!element) return;
    morph(element, fresh, (field, twin) => {
      const keepValue = field === document.activeElement || field.value !== posted[field.name];
      const value = field.value;
      copyAttributes(field, twin, ["value"]);
      if (field.defaultValue !== twin.defaultValue) field.defaultValue = twin.defaultValue;
      const wanted = keepValue ? value : twin.defaultValue;
      if (field.value !== wanted) field.value = wanted;
    });
    shown.editor = received.editor;
    for (const field of element.querySelectorAll(FIELD)) markEdit(field);
    schedulePreview(); // the status, actions and preview for the new baseline
  }

  function markEdit(field) {
    if (field.name === "lines") {
      const total = document.querySelector('[data-part="max-total-bet"]');
      const lines = field.value.replace(/,/g, "").trim();
      if (total) {
        total.textContent = /^\d+$/.test(lines)
          ? (Number(lines) * Number(total.dataset.perLine)).toLocaleString("en-US") + " credits"
          : "—";
      }
      return;
    }
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
