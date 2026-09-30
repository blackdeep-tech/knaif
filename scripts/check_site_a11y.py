#!/usr/bin/env python3
"""Browser-driven accessibility pass over the BUILT sites. Run with `just site-a11y`.

The static half of plan §9 (lang, viewport, one `<h1>`, no empty links, reduced-motion CSS
present) is checkable by reading HTML. The rest is not, and that is the whole reason this
file exists:

  * **Contrast** is a property of *computed* colour. Every colour on both sites arrives
    through a `var(--token)` that resolves differently per theme, and backgrounds are
    layered — the .org header is `color-mix(in srgb, var(--ground) 88%, transparent)` over
    whatever is beneath it. Reading the stylesheet tells you which token was named, not
    what the pixel ends up being.
  * **Keyboard order** is a property of the *rendered* document. DOM order, `tabindex`,
    visibility and disabled state together decide it, and the only honest way to know what
    a Tab press does is to press Tab.

So: serve each `dist/` on loopback, drive Chromium, and measure. Deliberately offline —
nothing here fetches a third-party URL, for the same reason `check_site_links.py` does not.

    uv run --group site-a11y playwright install chromium   # once
    just site-a11y

Checks, per site:

  contrast    every visible text run in BOTH themes, against its composited background,
              at the WCAG AA threshold for its size (4.5:1, or 3:1 for large text)
  focus ring  the focus indicator's colour against what surrounds it (3:1, non-text)
  keyboard    real Tab traversal: skip link first, order matches DOM order, every visible
              control reachable, no trap, every stop visibly focused
  responsive  no horizontal overflow at 360px
  motion      under `prefers-reduced-motion: reduce`, nothing is still animating

Findings are deduplicated by (element, colours) across pages: one bad token pair otherwise
reports 32 times and buries everything else.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

# (dist, default theme). The default is what the keyboard pass runs in — Tab order does not
# depend on colour, so traversing both themes would double the runtime to prove nothing.
SITES = {
    "knaif.org": (REPO / "site" / "org" / "dist", "light"),
    "knaif.dev": (REPO / "site" / "dev" / "dist", "dark"),
}

THEMES = ("light", "dark")

# WCAG 2.2 AA. Large text is >=24px, or >=18.66px at weight >=700.
AA_TEXT = 4.5
AA_LARGE = 3.0
AA_NON_TEXT = 3.0

# Both sites store the viewer's choice, and the pre-paint script in each layout reads it
# before we get a chance to touch the DOM. Seeding storage is what makes the theme stick
# through that script rather than fighting it afterwards.
THEME_KEYS = {"knaif.org": "knaif-theme", "knaif.dev": "starlight-theme"}


# --------------------------------------------------------------------------------------
# In-page measurement
# --------------------------------------------------------------------------------------

# Colours are normalised through a 1x1 canvas rather than by parsing strings. Chromium
# reports `color(srgb 1 1 1 / 0.88)` for a color-mix, `rgba(...)` for a literal, and would
# report `oklch(...)` verbatim for one of those; the canvas resolves all of them, with
# alpha, and cannot be caught out by a colour syntax nobody thought to handle.
CONTRAST_JS = r"""
(ringColor) => {
  const cvs = document.createElement("canvas");
  cvs.width = cvs.height = 1;
  const ctx = cvs.getContext("2d", { willReadFrequently: true });
  const cache = new Map();

  const rgba = (css) => {
    if (cache.has(css)) return cache.get(css);
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = css;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    const out = { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
    cache.set(css, out);
    return out;
  };

  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });

  const lum = ({ r, g, b }) => {
    const f = (c) => {
      c /= 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };

  const ratio = (x, y) => {
    const a = lum(x), b = lum(y);
    const [hi, lo] = a > b ? [a, b] : [b, a];
    return (hi + 0.05) / (lo + 0.05);
  };

  const hex = ({ r, g, b }) =>
    "#" + [r, g, b].map((c) => Math.round(c).toString(16).padStart(2, "0")).join("");

  const where = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 3; n = n.parentElement) {
      const cls = (n.getAttribute("class") || "").trim().split(/\s+/).filter(Boolean)[0];
      parts.unshift(
        n.tagName.toLowerCase() + (n.id ? "#" + n.id : "") + (cls ? "." + cls : "")
      );
      if (n.id) break;
    }
    return parts.join(" ");
  };

  // Everything painted behind `el`, composited top-down onto the canvas colour. Stops at
  // the first fully opaque layer. A background-image (including a gradient) is a colour we
  // cannot sample this way, so the run is reported as unmeasurable rather than guessed at.
  const backdrop = (el) => {
    const layers = [];
    let painted = false;
    for (let n = el; n; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.backgroundImage !== "none") return { color: null, imaged: where(n) };
      const c = rgba(cs.backgroundColor);
      const alpha = c.a * parseFloat(cs.opacity || "1");
      if (alpha > 0) {
        layers.push({ ...c, a: alpha });
        if (alpha >= 0.999) { painted = true; break; }
      }
    }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    if (!painted) {
      // Nothing opaque above the canvas. The canvas itself follows `color-scheme`.
      const scheme = getComputedStyle(document.documentElement).colorScheme || "";
      if (scheme.includes("dark")) base = { r: 18, g: 18, b: 18, a: 1 };
    }
    let out = base;
    for (const layer of layers.reverse()) out = over(layer, out);
    return { color: out, imaged: null };
  };

  // Hidden from sight but not from the a11y tree — skip-link and sr-only patterns. Their
  // colours are never seen, so measuring them only produces noise.
  const invisible = (el, cs) => {
    if (cs.visibility === "hidden" || cs.display === "none") return true;
    if (parseFloat(cs.opacity || "1") === 0) return true;
    if (cs.clipPath.startsWith("inset(50%")) return true;
    if (cs.clip && cs.clip !== "auto") return true;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) return true;
    return false;
  };

  const findings = [];
  const seen = new Set();

  // One measurement: `cs` supplies colour and size, `el` supplies the backdrop. `pseudo`
  // is "" for a real text node, "::before"/"::after" for generated content.
  const measure = (el, cs, pseudo, text) => {
    const size = parseFloat(cs.fontSize);
    const weight = parseInt(cs.fontWeight, 10) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const need = large ? 3.0 : 4.5;

    const back = backdrop(el);
    const label = where(el) + pseudo;
    if (!back.color) {
      const key = "img|" + label + "|" + back.imaged;
      if (seen.has(key)) return;
      seen.add(key);
      findings.push({ kind: "unmeasurable", where: label, over: back.imaged,
                      text: text.slice(0, 48) });
      return;
    }
    const fg = over(rgba(cs.color), back.color);
    const value = ratio(fg, back.color);
    const key = ["text", label, hex(fg), hex(back.color), Math.round(size)].join("|");
    if (seen.has(key)) return;
    seen.add(key);
    if (value + 0.005 >= need) return;
    findings.push({
      kind: "text", where: label, text: text.slice(0, 48),
      fg: hex(fg), bg: hex(back.color), size: Math.round(size * 10) / 10,
      weight, ratio: Math.round(value * 100) / 100, need,
    });
  };

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent.replace(/\s+/g, " ").trim();
    if (!text) continue;
    const el = node.parentElement;
    if (!el || el.closest("script, style, noscript, template, svg")) continue;
    const cs = getComputedStyle(el);
    if (invisible(el, cs)) continue;
    measure(el, cs, "", text);
  }

  // Generated content is invisible to a TreeWalker, and on this design system that is not
  // an edge case: the ENTIRE bracket motif -- section eyebrows and the pipeline diagram --
  // is `content: "["` on ::before/::after. Skipping pseudo-elements would leave the one
  // device the brand is built on unmeasured.
  for (const el of document.body.querySelectorAll("*")) {
    if (el.closest("script, style, noscript, template, svg")) continue;
    const own = getComputedStyle(el);
    if (invisible(el, own)) continue;
    for (const pseudo of ["::before", "::after"]) {
      const cs = getComputedStyle(el, pseudo);
      const content = cs.content;
      if (!content || content === "none" || content === "normal") continue;
      const text = content.replace(/^["']|["']$/g, "").replace(/\s+/g, " ").trim();
      if (!text || content.includes("url(")) continue;
      if (cs.display === "none" || cs.visibility === "hidden") continue;
      if (parseFloat(cs.opacity || "1") === 0) continue;
      measure(el, cs, pseudo, text);
    }
  }

  // The focus ring is non-text UI and needs 3:1 against whatever it is drawn over.
  //
  // `ringColor` is sampled from an element the harness has genuinely Tab-focused, not read
  // out of a token by name -- naming a token here would make this check agree with the
  // stylesheet it is supposed to audit, and it would keep passing after someone changed
  // which token `:focus-visible` uses. The ring is then tested against every ground token
  // rather than against the one element that happened to be focused, because it is a
  // single site-wide colour and `outline-offset` puts it on the surface BEHIND whatever
  // it rings -- a card, a nav bar, or the page.
  const root = getComputedStyle(document.documentElement);
  const rings = [];
  if (ringColor) {
    const ring = rgba(ringColor);
    for (const name of ["--ground", "--surface", "--subtle"]) {
      const raw = root.getPropertyValue(name).trim();
      if (!raw) continue;
      const bg = rgba(raw);
      if (bg.a < 0.999) continue;
      rings.push({
        on: name, color: hex(ring), bg: hex(bg),
        ratio: Math.round(ratio(over(ring, bg), bg) * 100) / 100,
      });
    }
  }

  return { findings, rings };
}
"""

# The ring colour has to come from a real Tab press: `:focus-visible` does not match a
# programmatic `.focus()`, so a scripted focus would read the unfocused outline.
RING_JS = r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  const cs = getComputedStyle(el);
  if (cs.outlineStyle === "none" || parseFloat(cs.outlineWidth) === 0) return null;
  return cs.outlineColor;
}
"""

# Assigns a stable marker to every element that could plausibly be a tab stop, so a Tab
# press can be turned back into "which element is this, and where was it in the source".
MARK_JS = r"""
() => {
  const sel = [
    "a[href]", "button", "input", "select", "textarea", "summary",
    "[tabindex]", "audio[controls]", "video[controls]", "[contenteditable]",
  ].join(",");
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const ti = el.getAttribute("tabindex");
    const hidden =
      cs.display === "none" || cs.visibility === "hidden" ||
      el.closest("[inert]") !== null || el.hasAttribute("disabled") ||
      el.closest("details:not([open])") !== null && el.tagName !== "SUMMARY";
    el.setAttribute("data-a11y-i", String(i));
    out.push({
      i, tag: el.tagName.toLowerCase(),
      id: el.id || null,
      cls: (el.getAttribute("class") || "").trim().split(/\s+/)[0] || null,
      label: (el.getAttribute("aria-label") || el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 40),
      tabindex: ti === null ? null : parseInt(ti, 10),
      hidden,
      offscreen: r.width === 0 && r.height === 0,
      href: el.getAttribute("href"),
    });
    i += 1;
  }
  return out;
}
"""

# Read after each Tab. `outline`/`boxShadow` is how "is this stop visibly focused" is
# answered — an element that takes focus with no indicator is a keyboard dead end even
# though every automated DOM check passes.
FOCUS_JS = r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  return {
    i: el.hasAttribute("data-a11y-i") ? parseInt(el.getAttribute("data-a11y-i"), 10) : null,
    tag: el.tagName.toLowerCase(),
    label: (el.getAttribute("aria-label") || el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 40),
    href: el.getAttribute("href"),
    outlineWidth: parseFloat(cs.outlineWidth) || 0,
    outlineStyle: cs.outlineStyle,
    boxShadow: cs.boxShadow,
    onScreen: r.bottom > 0 && r.top < innerHeight && r.width > 0 && r.height > 0,
  };
}
"""

OVERFLOW_JS = r"""
() => {
  const de = document.documentElement;
  if (de.scrollWidth <= de.clientWidth + 1) return null;
  const wide = [];
  for (const el of document.body.querySelectorAll("*")) {
    const r = el.getBoundingClientRect();
    if (r.right > de.clientWidth + 1 && getComputedStyle(el).position !== "fixed") {
      const cls = (el.getAttribute("class") || "").trim().split(/\s+/)[0];
      wide.push(el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (cls ? "." + cls : ""));
    }
  }
  return { scrollWidth: de.scrollWidth, clientWidth: de.clientWidth, widest: wide.slice(0, 4) };
}
"""

# getComputedTiming(), not getTiming(): a CSS animation reports its authored duration as
# the string "auto" from getTiming(), so `duration > 1` is false for every animation on the
# page and the check silently passes everything. Caught by injecting an animation that
# ignores the media query and watching this return nothing.
MOTION_JS = r"""
() => document.getAnimations()
  .filter((a) => a.playState === "running" &&
                 Number(a.effect?.getComputedTiming().duration || 0) > 1)
  .map((a) => a.animationName || "(unnamed)")
"""


# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pages: int = 0
    runs: int = 0
    stops: int = 0
    worst_text: float = 99.0

    def fail(self, message: str) -> None:
        self.problems.append(message)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass  # one line per asset across 62 page loads, saying nothing about the site


class _QuietServer(http.server.ThreadingHTTPServer):
    """Chromium drops connections routinely when a page is torn down mid-response, which
    the stdlib reports as an unhandled `ConnectionAbortedError` traceback. It says nothing
    about the site, and a gate that prints tracebacks on a clean run is one people stop
    reading."""

    def handle_error(self, *args: Any) -> None:
        pass


def serve(directory: Path) -> tuple[str, http.server.HTTPServer]:
    """Serve `directory` on an ephemeral loopback port.

    Astro emits `route/index.html`, so directory indexes are the routing — `file://` would
    404 on every internal link and make the whole traversal meaningless.
    """
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = _QuietServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.socket.getsockname()[:2]
    if ":" in host:  # pragma: no cover - IPv6 loopback
        host = f"[{host}]"
    return f"http://{host}:{port}", server


def routes(dist: Path) -> list[str]:
    found = sorted(
        ("/" + page.parent.relative_to(dist).as_posix().strip(".") + "/").replace("//", "/")
        for page in dist.rglob("index.html")
    )
    # Starlight emits a 404 page, which is a page a visitor genuinely lands on and the one
    # page no route enumeration finds. Counting only index.html would leave it unchecked.
    if (dist / "404.html").is_file():
        found.append("/404.html")
    return found


def audit_contrast(page: Any, site: str, route: str, theme: str, report: Report) -> None:
    page.keyboard.press("Tab")
    ring_color = page.evaluate(RING_JS)
    if ring_color is None:
        report.notes.append(f"{site} {theme} {route}: first tab stop draws no outline")

    result = page.evaluate(CONTRAST_JS, ring_color)
    for finding in result["findings"]:
        if finding["kind"] == "unmeasurable":
            report.notes.append(
                f"{site} {theme} {route}: {finding['where']} sits on a background image "
                f"({finding['over']}) - contrast not machine-measurable"
            )
            continue
        report.worst_text = min(report.worst_text, finding["ratio"])
        report.fail(
            f"{site} {theme} {route}: {finding['where']} "
            f"{finding['fg']} on {finding['bg']} = {finding['ratio']}:1 "
            f"(needs {finding['need']}:1 at {finding['size']}px/{finding['weight']}) "
            f'"{finding["text"]}"'
        )
    for ring in result["rings"]:
        if ring["ratio"] < AA_NON_TEXT:
            report.fail(
                f"{site} {theme} {route}: focus ring {ring['color']} on {ring['on']} "
                f"{ring['bg']} = {ring['ratio']}:1 (needs {AA_NON_TEXT}:1, non-text)"
            )


def audit_keyboard(page: Any, site: str, route: str, report: Report) -> None:
    candidates = page.evaluate(MARK_JS)
    expected = [c for c in candidates if not c["hidden"] and (c["tabindex"] or 0) >= 0]

    for c in candidates:
        if (c["tabindex"] or 0) > 0:
            report.fail(
                f"{site} {route}: positive tabindex={c['tabindex']} on "
                f"<{c['tag']}> \"{c['label']}\" - it jumps the queue ahead of the document"
            )

    # The contrast pass leaves focus on the first stop, and `blur()` is not enough to undo
    # that: blurring clears focus but LEAVES the sequential navigation starting point where
    # it was, so the next Tab lands on stop two and every page reports a missing skip link.
    # Focusing <body> is what actually moves the starting point — hence the temporary
    # tabindex, since <body> is not focusable without one.
    page.evaluate("""() => {
            document.body.setAttribute("tabindex", "-1");
            document.body.focus();
            document.body.removeAttribute("tabindex");
            window.scrollTo(0, 0);
        }""")
    page.keyboard.press("Tab")

    order: list[dict] = []
    seen_indices: set[int] = set()
    limit = len(expected) + 8
    for _ in range(limit):
        stop = page.evaluate(FOCUS_JS)
        if stop is None:
            break
        if order and stop["i"] is not None and stop["i"] == order[-1]["i"]:
            report.fail(
                f"{site} {route}: Tab does not advance past <{stop['tag']}> "
                f"\"{stop['label']}\" - keyboard trap"
            )
            break
        if stop["i"] is not None and stop["i"] in seen_indices:
            break  # wrapped around; the cycle is complete
        if stop["i"] is not None:
            seen_indices.add(stop["i"])
        order.append(stop)
        page.keyboard.press("Tab")

    report.stops += len(order)

    if not order:
        report.fail(f"{site} {route}: Tab reaches nothing at all")
        return

    first = order[0]
    if not (first["href"] or "").startswith("#"):
        report.fail(
            f"{site} {route}: first tab stop is <{first['tag']}> \"{first['label']}\", "
            "not a skip link"
        )
    else:
        target = first["href"][1:]
        if not page.evaluate("(id) => !!document.getElementById(id)", target):
            report.fail(f"{site} {route}: skip link targets #{target}, which does not exist")
        if not first["onScreen"]:
            report.fail(
                f"{site} {route}: skip link \"{first['label']}\" stays off-screen while "
                "focused - a sighted keyboard user cannot see where they are"
            )

    previous = -1
    for stop in order:
        if stop["i"] is None:
            continue
        if stop["i"] < previous:
            report.fail(
                f"{site} {route}: tab order leaves document order at <{stop['tag']}> "
                f"\"{stop['label']}\""
            )
        previous = stop["i"]
        indicated = (
            stop["outlineStyle"] not in ("none", "") and stop["outlineWidth"] >= 1
        ) or stop["boxShadow"] not in ("none", "")
        if not indicated:
            report.fail(
                f"{site} {route}: <{stop['tag']}> \"{stop['label']}\" takes focus with no "
                "visible indicator"
            )

    unreached = [c for c in expected if c["i"] not in seen_indices and not c["offscreen"]]
    for c in unreached:
        report.fail(
            f"{site} {route}: <{c['tag']}> \"{c['label']}\" is visible but never receives "
            "focus while tabbing"
        )


def audit_page(
    browser: Any, site: str, base: str, route: str, default: str, report: Report
) -> None:
    key = THEME_KEYS[site]
    for theme in THEMES:
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_init_script(
            f"try {{ localStorage.setItem({key!r}, {theme!r}); }} catch (e) {{}}"
        )
        page = context.new_page()
        page.goto(base + route, wait_until="load")
        page.evaluate("(t) => { document.documentElement.dataset.theme = t; }", theme)
        report.runs += 1

        audit_contrast(page, site, route, theme, report)

        if theme == default:
            audit_keyboard(page, site, route, report)

            page.set_viewport_size({"width": 360, "height": 720})
            overflow = page.evaluate(OVERFLOW_JS)
            if overflow:
                report.fail(
                    f"{site} {route}: scrolls sideways at 360px "
                    f"({overflow['scrollWidth']}px wide) - {', '.join(overflow['widest'])}"
                )

        context.close()


def audit_motion(browser: Any, site: str, base: str, route: str, report: Report) -> None:
    context = browser.new_context(reduced_motion="reduce")
    page = context.new_page()
    page.goto(base + route, wait_until="load")
    page.wait_for_timeout(200)
    running = page.evaluate(MOTION_JS)
    if running:
        report.fail(
            f"{site} {route}: still animating under prefers-reduced-motion "
            f"({', '.join(sorted(set(running)))})"
        )
    context.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", choices=sorted(SITES), help="check one site only")
    parser.add_argument("--route", action="append", help="check one route only (repeatable)")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        print(
            "playwright is not installed. It is a dev-only group so the default\n"
            "environment stays light:\n"
            "    uv sync --group site-a11y\n"
            "    uv run --group site-a11y playwright install chromium",
            file=sys.stderr,
        )
        return 2

    report = Report()
    targets = {k: v for k, v in SITES.items() if args.site in (None, k)}

    missing = [
        f"{site}: {dist.relative_to(REPO).as_posix()} missing - run `just site-build`"
        for site, (dist, _) in targets.items()
        if not dist.is_dir()
    ]
    if missing:
        for problem in missing:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1

    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=not args.headed)
        try:
            for site, (dist, default) in targets.items():
                base, server = serve(dist)
                try:
                    pages = routes(dist)
                    if args.route:
                        pages = [r for r in pages if r in args.route]
                    if not pages:
                        report.fail(f"{site}: no routes matched")
                        continue
                    report.pages += len(pages)
                    for route in pages:
                        audit_page(browser, site, base, route, default, report)
                    audit_motion(browser, site, base, pages[0], report)
                finally:
                    server.shutdown()
                    server.server_close()
        finally:
            browser.close()

    for note in report.notes:
        print(f"note {note}")
    for problem in report.problems:
        print(f"FAIL {problem}", file=sys.stderr)

    if report.problems:
        print(
            f"\n{len(report.problems)} problem(s) across {report.pages} pages "
            f"({report.runs} page/theme runs, {report.stops} tab stops)",
            file=sys.stderr,
        )
        return 1

    print(
        f"ok  {report.pages} pages x {len(THEMES)} themes: contrast clears AA, "
        f"{report.stops} tab stops in document order and visibly focused, "
        f"no overflow at 360px, motion stops under reduce"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
