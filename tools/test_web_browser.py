"""The web control page in Chromium (decision-2 r2, r7): the real web_app and web_worker,
started by web_control.start on an ephemeral loopback port, serving the combined fake machine
(sas_testing.FakeS6000) with a temporary journal.

pytest-playwright suite, not a stdlib unittest module: run it with
`python -m pytest tools/test_web_browser.py` where pytest-playwright and its Chromium are
installed (the dev container). Without playwright the module is skipped.

Every wait is for a page or machine event (Playwright's expect and wait_for_function, the
worker's snapshot subscription); GUARD only bounds a wait that a working page never needs.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterator

import pytest
import web_control
import web_state as ws
from sas_testing import EXTENSION_POLL, SPIN, FakeS6000

try:
    from playwright.sync_api import Browser, Locator, Page, expect
except ImportError:
    pytest.skip("needs playwright and its Chromium (pytest-playwright)", allow_module_level=True)

LOOPBACK = "127.0.0.1"
DEVICE = "fake-serial-device"  # handed to open_line, which ignores it
GUARD = 20.0  # seconds
GUARD_MS = GUARD * 1000
LAPTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
AMOUNT = 25  # credits a test adds

expect.set_options(timeout=GUARD_MS)

# Counts the event-stream messages each page receives, and the regions each one carried. Its
# listener is added when the stream is made, before live.js sets onmessage, so a count seen
# by the test means live.js has already applied that message.
LIVE_PROBE = """
(() => {
  const Native = window.EventSource;
  window.__live = { snapshots: 0, regions: [] };
  window.EventSource = class extends Native {
    constructor(...args) {
      super(...args);
      this.addEventListener("message", (event) => {
        window.__live.regions.push(Object.keys(JSON.parse(event.data).regions));
        window.__live.snapshots += 1;
      });
    }
  };
})();
"""

# A submit button that posts an action (the odds preview sends nothing): its own formaction,
# else its form's action, names the route.
ACTS = """
(el) => {
  if (el.tagName !== "BUTTON" || el.type !== "submit" || el.form === null || el.closest("noscript")) return false;
  const route = new URL(el.hasAttribute("formaction") ? el.formAction : el.form.action).pathname;
  return route.startsWith("/actions/") && route !== "/actions/preview";
}
"""
# Every control that sends something to the machine: action buttons and the Add credits key,
# a link to its sheet while a top-up is admissible. `on` is whether a person can use it now.
MACHINE_CONTROLS = f"""
() => {{
  const acts = (el) => el.dataset.part === "add-credits" || ({ACTS})(el);
  return Array.from(document.querySelectorAll("button, a[data-part='add-credits']")).filter(acts)
    .map((el) => ({{
      part: el.dataset.part || el.name,
      on: el.tagName === "A" ? el.getAttribute("aria-disabled") !== "true" : !el.disabled,
    }}));
}}
"""
ALL_OFF = f"() => {{ const controls = ({MACHINE_CONTROLS})(); return controls.length > 0 && controls.every((c) => !c.on); }}"

# What a person must be able to reach at every width: machine controls, the editor's fields
# and the page navigation. Other links (a side panel's close button) may be hidden by design
# at one width.
CONTROLS = "a[href], button, input:not([type=hidden])"
REQUIRED = f"""
(el) => el.matches('[data-part^="nav-"], [data-part="add-credits"], input') || ({ACTS})(el)
"""
# The name of the odds editor's first award field above 1, which a test can lower and still
# have a profile the machine takes.
FIRST_PAYING_AWARD = """
() => Array.from(document.querySelectorAll('input[name^="award-"]'))
  .find((el) => Number(el.value.replace(/,/g, "")) > 1).name
"""
# Each page and sheet, and the part of it whose controls a person uses there: behind an open
# sheet the page is covered on purpose.
SCREENS = (("/", "body"), ("/odds", "body"), ("/activity", "body"), ("/settings", "body"),
           ("/?sheet=next-result", '[data-region="next-result"]'),
           ("/?sheet=add-credits", '[data-region="add-credits"]'))


class Machine(FakeS6000):
    """The combined fake machine, whose line can stop right after the request for one
    extension op (`hold_after`) until `release`, so a test sees the page while the worker
    waits on the machine."""

    def __init__(self):
        super().__init__()
        self.hold_after: set[int] = set()
        self._flowing = threading.Event()
        self._flowing.set()

    def send(self, first, rest):
        self._flowing.wait()
        super().send(first, rest)
        requests = self.extension.requests
        if rest[:1] == bytes((EXTENSION_POLL,)) and requests and requests[-1][0] in self.hold_after:
            self.hold_after.discard(requests[-1][0])
            self._flowing.clear()

    def release(self) -> None:
        self._flowing.set()

    @property
    def credits(self) -> int:
        return self.meters[0x0C]

    def sent_ops(self, op: int) -> int:
        return sum(1 for request in self.extension.mutations() if request[0] == op)


class Site:
    def __init__(self, machine: Machine, running: web_control.Running):
        self.machine = machine
        self.running = running
        self.url = f"http://{LOOPBACK}:{running.server.port}"

    def wait_for(self, ready: Callable[[ws.MachineState], bool]) -> None:
        worker = self.running.worker
        subscription = worker.subscribe()
        while not ready(worker.snapshot()):
            assert subscription.wait(GUARD) is not None, "the worker never got there"


class Pages:
    """Browser pages on the site, each in its own context (a separate device), with the live
    probe installed; prompts and script errors are recorded."""

    def __init__(self, browser: Browser, site: Site):
        self.browser = browser
        self.site = site
        self.contexts = []
        self.dialogs: list[str] = []
        self.errors: list[str] = []

    def open(self, path: str, viewport: dict = LAPTOP, **options) -> Page:
        context = self.browser.new_context(base_url=self.site.url, viewport=viewport, **options)
        self.contexts.append(context)
        context.set_default_timeout(GUARD_MS)
        context.add_init_script(script=LIVE_PROBE)
        page = context.new_page()
        page.on("dialog", self._dialog)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        visit(page, path)
        return page

    def _dialog(self, dialog) -> None:
        self.dialogs.append(dialog.type)
        dialog.dismiss()

    def close(self) -> None:
        for context in self.contexts:
            context.close()


@pytest.fixture
def site(tmp_path) -> Iterator[Site]:
    machine = Machine()
    settings = web_control.parse(["--port", DEVICE, "--listen", f"{LOOPBACK}:0",
                                  "--journal", str(tmp_path / "journal")])
    running = web_control.start(settings, open_line=lambda device: machine)
    site = Site(machine, running)
    try:
        site.wait_for(lambda state: state.fresh)
        yield site
    finally:
        machine.release()  # a failed test may have left the line held
        running.stop()
    assert not running.failed


@pytest.fixture
def pages(browser: Browser, site: Site) -> Iterator[Pages]:
    pages = Pages(browser, site)
    yield pages
    pages.close()
    assert pages.errors == []


def visit(page: Page, path: str) -> None:
    """Load path and wait until live.js has applied the stream's first snapshot: before it,
    live.js holds every action back."""
    page.goto(path)
    page.wait_for_function("() => window.__live.snapshots > 0")


def snapshots(page: Page) -> int:
    return page.evaluate("() => window.__live.snapshots")


def part(page: Page, name: str) -> Locator:
    return page.locator(f'[data-part="{name}"]')


def banner(page: Page) -> Locator:
    return page.locator('[data-region="banner"]')


def credit_meter(page: Page) -> Locator:
    return page.locator('[data-region="meters"] #credit-meter .meter__value')


def credits_text(credits: int) -> str:
    return f"{credits:,}"


def whole(text: str) -> int:
    return int(text.replace(",", ""))


def edit_weight(page: Page) -> tuple[Locator, str]:
    """Raise the odds editor's first weight by one and leave the field: an edited draft that
    the server's preview makes appliable."""
    weight = page.locator('input[name="w-0-0"]')
    typed = str(whole(weight.input_value()) + 1)
    weight.fill(typed)
    weight.blur()  # protected because it is edited, not merely because it has focus
    expect(part(page, "apply-odds")).to_be_enabled()
    return weight, typed


def machine_controls(page: Page) -> list[dict]:
    return page.evaluate(MACHINE_CONTROLS)


def add_credits(page: Page, amount: int) -> None:
    """Enter amount in the open Add credits sheet and tap its button."""
    page.locator('[data-region="add-credits"] input[name="amount"]').fill(str(amount))
    part(page, "add-credits-submit").click()


def test_a_deposit_on_one_page_shows_on_another_open_page_without_a_reload(site, pages):
    before = site.machine.credits
    acting = pages.open("/?sheet=add-credits")
    watching = pages.open("/")
    watching.evaluate("() => { window.__notReloaded = true; }")
    expect(credit_meter(watching)).to_have_text(credits_text(before))
    add_credits(acting, AMOUNT)
    expect(credit_meter(watching)).to_have_text(credits_text(before + AMOUNT))
    assert watching.evaluate("() => window.__notReloaded") is True
    assert site.machine.credits == before + AMOUNT


def test_add_credits_and_spin_act_on_one_tap_without_any_prompt(site, pages):
    before = site.machine.credits
    page = pages.open("/?sheet=add-credits")
    add_credits(page, AMOUNT)
    expect(credit_meter(page)).to_have_text(credits_text(before + AMOUNT))
    assert site.machine.credits == before + AMOUNT

    visit(page, "/")
    last_spin = page.locator('[data-region="last-spin"]')
    shown = last_spin.text_content()
    part(page, "spin").click()
    expect(last_spin).not_to_have_text(shown)
    assert site.machine.game_meters["games_played"] == 1
    assert site.machine.sent_ops(SPIN) == 1
    assert pages.dialogs == []


def test_a_lost_reply_holds_its_family_as_checking_until_the_page_knows(site, pages):
    machine = site.machine
    page = pages.open("/")
    machine.lose.add(SPIN)  # the machine plays the spin, but its answer never arrives
    machine.hold_after.add(SPIN)  # and the line stops after it until the test has looked
    part(page, "spin").click()
    expect(banner(page)).to_have_attribute("data-banner", "checking")
    expect(part(page, "spin")).to_be_disabled()
    expect(part(page, "add-credits")).to_be_enabled()  # another family is not held

    machine.lose.discard(SPIN)
    machine.release()
    expect(banner(page)).not_to_have_attribute("data-banner", "checking")
    expect(part(page, "spin")).to_be_enabled()
    assert machine.sent_ops(SPIN) == 1  # never resent
    assert machine.game_meters["games_played"] == 1


def test_a_machine_that_goes_offline_turns_every_control_off_on_every_page(site, pages):
    opened = [pages.open(path) for path in ("/", "/odds", "/settings")]
    play, odds, _ = opened
    edit_weight(odds)  # an edited draft makes Apply usable, and its regions are protected
    for page in opened:
        assert any(control["on"] for control in machine_controls(page)), page.url

    site.machine.power_off()
    expect(banner(play)).to_have_attribute("data-banner", "offline")
    for page in opened:
        expect(page.locator('[data-region="status"]')).to_have_class(re.compile(r"status-pill--danger"))
        page.wait_for_function(ALL_OFF)

    site.machine.power_on()
    for page in opened:
        page.wait_for_function(f"() => ({MACHINE_CONTROLS})().some((c) => c.on)")


def test_a_stopped_server_shows_unreachable_and_turns_every_control_off(site, pages):
    opened = [pages.open(path) for path in ("/", "/settings")]
    play = opened[0]
    expect(part(play, "spin")).to_be_enabled()

    site.running.stop()
    expect(banner(play)).to_have_attribute("data-banner", "unreachable")
    for page in opened:
        page.wait_for_function(ALL_OFF)
        assert not any(control["on"] for control in machine_controls(page)), page.url


def test_a_live_update_leaves_an_edited_odds_draft_alone(site, pages):
    editing = pages.open("/odds")
    applying = pages.open("/odds")

    weight, typed = edit_weight(editing)
    head = editing.locator('[data-region="odds-head"]')
    shown = head.text_content()
    seen = snapshots(editing)

    award = applying.locator(f'input[name="{applying.evaluate(FIRST_PAYING_AWARD)}"]')
    award.fill(str(whole(award.input_value()) - 1))
    expect(part(applying, "apply-odds")).to_be_enabled()
    part(applying, "apply-odds").click()

    editing.wait_for_function(
        "seen => window.__live.regions.slice(seen).some((names) => names.includes('editor'))", arg=seen)
    expect(head).not_to_have_text(shown)  # the update reached this page
    expect(weight).to_have_value(typed)  # and left the draft as it was


def test_phone_play_shows_the_reels_readings_and_every_play_button_without_scrolling(site, pages):
    page = pages.open("/", PHONE, is_mobile=True, has_touch=True)
    assert page.evaluate("() => window.scrollY") == 0
    keys = page.locator('[data-region="deck"] .key')
    assert keys.count() > 0
    for index in range(keys.count()):
        expect(keys.nth(index)).to_be_in_viewport(ratio=1)
    expect(part(page, "spin")).to_be_in_viewport(ratio=1)
    for one_tap_away in ("details-open", "next-result-toggle", "add-credits"):
        expect(part(page, one_tap_away)).to_be_in_viewport(ratio=1)
    expect(page.locator('[data-region="reels"]')).to_be_in_viewport(ratio=1)
    expect(page.locator('[data-region="meters"]')).to_be_in_viewport(ratio=1)


@pytest.mark.parametrize("viewport", [LAPTOP, PHONE], ids=["laptop", "phone"])
def test_every_control_is_reachable_and_usable(site, pages, viewport):
    page = pages.open("/", viewport)
    for path, scope in SCREENS:
        visit(page, path)
        controls = page.locator(scope).locator(CONTROLS)
        assert controls.count() > 0, path
        for index in range(controls.count()):
            control = controls.nth(index)
            where = f"{path} {control.evaluate('(el) => el.outerHTML.slice(0, 120)')}"
            if not control.is_visible():
                assert not control.evaluate(REQUIRED), f"hidden: {where}"
                continue
            if control.is_enabled():
                control.click(trial=True)  # visible, stable, enabled and not covered
            else:
                control.scroll_into_view_if_needed()
                expect(control, where).to_be_in_viewport()
