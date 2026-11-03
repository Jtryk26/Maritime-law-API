"""Layout-audit af sølov.dk-frontenden: telefon og skrivebord.

Måler for hver side × bredde × tema:
  a. vandret overløb (scrollWidth mod clientWidth) og de 5 bredeste syndere
  b. skærmudnyttelse: venstre/højre kant af hovedindholdet og hovedtekstspalten
  c. skriftstørrelser: synlige tegn pr. beregnet skriftstørrelse; tekst <= 13 px
  d. linjelængde: gennemsnitlige tegn pr. linje for brødtekst og lovtekst
  e. kontrast: tekst under 4,5:1 (normal) eller 3:1 (stor tekst)
  f. kun 390 px: trykflader under 24 × 24 px

Skriver viewport-skærmbilleder <side>-<bredde>-<tema>.png, results.json og
README.md med oversigtstabeller til --out.

Kræver Playwright for Python (``pip install playwright`` og
``python -m playwright install chromium``). Eksempler:

    python audit/run_audit.py --out audit/before --base-url http://127.0.0.1:5173
    python audit/run_audit.py --out audit/live --base-url https://xn--slov-gra.dk \\
        --doc-id 2423 --widths 390,1440 --themes light --pages front,document,applicability

Scriptet læser kun. Det eneste, der sendes til serveren, er de kald, siderne
selv laver ved indlæsning (bl.a. Skibsvurderingens standardvurdering).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = {
    "front": "#/",
    "search": "#/?q=hviletid",
    "document": "#/dokument/{doc_id}?p=p-1",
    "applicability": "#/anvendelighed",
}
DEFAULT_WIDTHS = (390, 768, 1024, 1440, 1920)
DEFAULT_THEMES = ("light", "dark")
HEIGHT = 900
SMALL_FONT_PX = 13
TAP_MIN_PX = 24
LINE_MIN, LINE_MAX, LINE_CHECK_FROM = 45, 90, 1024
ATTEMPTS = 3
PHONE_MAX = 860  # Skibsvurderingens telefonlayout (formularen er sammenklappet)

# Hovedtekstspalten pr. side: den første selector, der findes, bruges.
TEXT_COLUMN = {
    "front": [".search-hero", ".app > main > *"],
    "search": [".results-list", ".layout", ".search-hero"],
    "document": [".paragraph", ".doc-body"],
    "applicability": [".applicability-content"],
}

MEASURE_JS = r"""
({textColumn, tapMin, smallFont}) => {
  const vw = document.documentElement.clientWidth;
  const sw = document.documentElement.scrollWidth;
  const round = (n) => Math.round(n * 10) / 10;

  const describe = (el) => {
    const parts = [];
    let node = el;
    for (let i = 0; node && node.nodeType === 1 && i < 3; i++, node = node.parentElement) {
      let s = node.tagName.toLowerCase();
      if (node.id) s += '#' + node.id;
      const cls = [...node.classList].slice(0, 2);
      if (cls.length) s += '.' + cls.join('.');
      parts.unshift(s);
      if (node.id) break;
    }
    return parts.join(' > ');
  };

  const visible = (el) => {
    if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };

  // Elementer, der klipper vandret, forhindrer at efterkommere skaber sideoverløb.
  const clipsX = (el) => ['hidden', 'auto', 'scroll', 'clip'].includes(getComputedStyle(el).overflowX);
  const clippedByAncestor = (el) => {
    for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
      if (p === document.body) return false;
      if (clipsX(p)) return true;
    }
    return false;
  };

  // a. Overløb
  const offenders = [];
  for (const el of document.body.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const over = Math.max(r.right - vw, -r.left);
    if (over > 1 && !clippedByAncestor(el)) {
      offenders.push({selector: describe(el), width: round(r.width), left: round(r.left),
                      right: round(r.right), overflow_px: round(over)});
    }
  }
  // Behold kun de yderste syndere: et barn af en synder er sjældent årsagen.
  offenders.sort((a, b) => b.width - a.width || b.overflow_px - a.overflow_px);

  // b. Skærmudnyttelse
  const edges = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {selector: describe(el), left: round(r.left), right: round(r.right), width: round(r.width),
            left_pct: round(100 * r.left / vw), right_pct: round(100 * r.right / vw),
            width_pct: round(100 * r.width / vw)};
  };
  const mainEl = document.querySelector('.app > main') || document.querySelector('main');
  let colEl = null, colSelector = null;
  for (const sel of textColumn) {
    const el = document.querySelector(sel);
    if (el && visible(el)) { colEl = el; colSelector = sel; break; }
  }

  // Farver: canvas oversætter enhver CSS-farve (oklch, color-mix …) til sRGB.
  const ctx = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  const rgba = (css) => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = '#000'; ctx.fillStyle = css;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const blend = (top, bottom) => {
    const a = top[3];
    return [0, 1, 2].map((i) => top[i] * a + bottom[i] * (1 - a)).concat(1);
  };
  const backgroundOf = (el) => {
    const layers = [];
    for (let n = el; n; n = n.parentElement) {
      const c = rgba(getComputedStyle(n).backgroundColor);
      if (c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
    }
    let bg = [255, 255, 255, 1];
    for (const layer of layers.reverse()) bg = blend(layer, bg);
    return bg;
  };
  const opacityOf = (el) => {
    let o = 1;
    for (let n = el; n; n = n.parentElement) o *= parseFloat(getComputedStyle(n).opacity);
    return o;
  };
  const lum = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const hex = (c) => '#' + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');

  // c + e. Tekstnoder
  const fontChars = {};
  const contrast = {};
  let totalChars = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const styleCache = new Map();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent.replace(/\s+/g, '');
    if (!text) continue;
    const el = node.parentElement;
    if (!el || ['SCRIPT', 'STYLE', 'NOSCRIPT', 'OPTION'].includes(el.tagName)) continue;
    if (!visible(el)) continue;
    let info = styleCache.get(el);
    if (!info) {
      const cs = getComputedStyle(el);
      const size = parseFloat(cs.fontSize);
      const weight = parseInt(cs.fontWeight, 10) || 400;
      const bg = backgroundOf(el);
      const fgRaw = rgba(cs.color);
      const fg = blend([fgRaw[0], fgRaw[1], fgRaw[2], fgRaw[3] * opacityOf(el)], bg);
      info = {size, weight, bg, fg, r: ratio(fg, bg)};
      styleCache.set(el, info);
    }
    const n = text.length;
    totalChars += n;
    const key = String(Math.round(info.size * 10) / 10);
    fontChars[key] = (fontChars[key] || 0) + n;

    // Rent dekorative skilletegn tæller ikke i kontrastmålingen.
    if (/^[·•|/—–\-•:]+$/.test(text)) continue;
    const large = info.size >= 24 || (info.size >= 18.66 && info.weight >= 700);
    const need = large ? 3 : 4.5;
    if (info.r < need) {
      const sel = describe(el);
      const k = sel + '|' + hex(info.fg) + '|' + hex(info.bg);
      const e = contrast[k] || (contrast[k] = {selector: sel, fg: hex(info.fg), bg: hex(info.bg),
        ratio: Math.round(info.r * 100) / 100, required: need, font_px: info.size,
        sample: node.textContent.trim().slice(0, 60), chars: 0});
      e.chars += n;
    }
  }
  const smallChars = Object.entries(fontChars)
    .filter(([s]) => parseFloat(s) <= smallFont).reduce((a, [, n]) => a + n, 0);

  // d. Linjelængde: "bladblokke" med egen tekst og ingen blokke indeni.
  const blockish = (d) => ['block', 'list-item', 'flex', 'grid', 'table', 'table-cell', 'flow-root'].includes(d);
  const lines = {body: {chars: 0, lines: 0, blocks: 0, full: []}, law: {chars: 0, lines: 0, blocks: 0, full: []}};
  // Tegn pr. linje for alle linjer undtagen blokkens sidste. Gennemsnittet
  // ovenfor trækkes ned af korte slutlinjer; medianen af fulde linjer viser
  // spaltens faktiske linjelængde.
  const fullLines = (el, tol) => {
    const counts = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      for (let i = 0; i < n.length; i++) {
        range.setStart(n, i); range.setEnd(n, i + 1);
        const b = range.getClientRects()[0];
        if (!b) continue;
        const hit = counts.find((c) => Math.abs(c.top - b.top) < tol);
        if (hit) hit.n += 1; else counts.push({top: b.top, n: 1});
      }
    }
    return counts.sort((a, b) => a.top - b.top).slice(0, -1).map((c) => c.n);
  };
  const median = (v) => (v.length ? [...v].sort((a, b) => a - b)[Math.floor(v.length / 2)] : null);
  const scope = mainEl || document.body;
  for (const el of scope.querySelectorAll('p, li, dd, div, span, blockquote, td')) {
    const cs = getComputedStyle(el);
    if (!blockish(cs.display)) continue;
    if (el.closest('h1, h2, h3, h4, h5, h6, button, label, select, nav, header, form, .toc, .doc-toc')) continue;
    const hasOwnText = [...el.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim());
    if (!hasOwnText || !visible(el)) continue;
    if ([...el.querySelectorAll('*')].some((c) => blockish(getComputedStyle(c).display))) continue;
    const text = el.innerText.replace(/\s+/g, ' ').trim();
    if (text.length < 40) continue;
    const range = document.createRange();
    range.selectNodeContents(el);
    const tops = [];
    const tol = parseFloat(cs.fontSize) / 2;
    for (const r of range.getClientRects()) {
      if (r.width < 1) continue;
      if (!tops.some((t) => Math.abs(t - r.top) < tol)) tops.push(r.top);
    }
    if (tops.length < 2) continue;  // én linje siger intet om spaltebredden
    const bucket = el.closest('.paragraph') ? lines.law : lines.body;
    bucket.chars += text.length; bucket.lines += tops.length; bucket.blocks += 1;
    if (bucket.blocks <= 300) bucket.full.push(...fullLines(el, tol));
  }
  const cpl = (b) => (b.lines ? round(b.chars / b.lines) : null);

  // f. Trykflader
  const taps = [];
  for (const el of document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [tabindex]:not([tabindex="-1"])')) {
    if (!visible(el)) continue;
    // Et felt inde i en <label> aktiveres også af etiketten (WCAG 2.5.8 regner
    // etiketten med i målet), så etikettens flade er den, der måles.
    const label = el.tagName === 'INPUT' ? el.closest('label') : null;
    const r = (label || el).getBoundingClientRect();
    if (r.width >= tapMin && r.height >= tapMin) continue;
    const cs = getComputedStyle(el);
    // Links inde i løbende tekst er undtaget i WCAG 2.5.8; de markeres særskilt.
    const inline = el.tagName === 'A' && cs.display === 'inline' && !!el.closest('p, li, dd, .snippet, .paragraph');
    taps.push({selector: describe(el), width: round(r.width), height: round(r.height), inline, via_label: !!label,
               text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 40)});
  }

  return {
    viewport_width: vw,
    scroll_width: sw,
    overflow_px: Math.max(0, sw - vw),
    overflow_offenders: offenders.slice(0, 5),
    main: edges(mainEl),
    text_column: colEl ? Object.assign(edges(colEl), {matched: colSelector}) : null,
    fonts: {total_chars: totalChars, chars_by_px: fontChars, small_chars: smallChars,
            small_pct: totalChars ? round(100 * smallChars / totalChars) : 0},
    line_length: {body_cpl: cpl(lines.body), body_blocks: lines.body.blocks,
                  law_cpl: cpl(lines.law), law_blocks: lines.law.blocks,
                  body_full_median: median(lines.body.full), law_full_median: median(lines.law.full)},
    contrast_failures: Object.values(contrast).sort((a, b) => b.chars - a.chars),
    tap_targets_small: taps,
  };
}
"""


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, type=Path, help="Mappe til skærmbilleder og resultater.")
    p.add_argument("--base-url", required=True, help="F.eks. http://127.0.0.1:5173")
    p.add_argument("--doc-id", default="1", help="Dokument til dokumentsiden (standard: 1).")
    p.add_argument("--widths", default=",".join(map(str, DEFAULT_WIDTHS)))
    p.add_argument("--themes", default=",".join(DEFAULT_THEMES))
    p.add_argument("--pages", default=",".join(PAGES), help="Kommasepareret: " + ", ".join(PAGES))
    p.add_argument("--label", default=None, help="Fri tekst til README, f.eks. git-revision.")
    p.add_argument("--pause", type=float, default=0.0, help="Sekunder mellem sideindlæsninger.")
    p.add_argument("--open-applicability-form", action="store_true",
                   help="Skibsvurdering ≤ 860 px: klik 'Ret fartøjsprofil' efter skærmbilledet og før "
                        "målingen, så den sammenklappede formular også måles. Formularen gemmes som "
                        "<side>-<bredde>-<tema>-form.png.")
    return p.parse_args(argv)


def open_applicability_form(page, width: int, page_key: str, shot: Path) -> bool:
    if page_key != "applicability" or width > PHONE_MAX:
        return False
    toggle = page.locator(".profile-toggle")
    if not toggle.count() or not toggle.is_visible():
        return False
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
        page.wait_for_timeout(300)
    page.locator("#profile-builder").scroll_into_view_if_needed()
    page.screenshot(path=str(shot.with_name(shot.stem + "-form.png")), full_page=False)
    page.evaluate("window.scrollTo(0, 0)")
    return True


def measure(browser, url: str, width: int, theme: str, page_key: str, shot: Path,
            open_form: bool = False) -> dict:
    context = browser.new_context(viewport={"width": width, "height": HEIGHT},
                                  color_scheme=theme, device_scale_factor=1, locale="da-DK")
    page = context.new_page()
    console_errors: list[str] = []
    page.on("console", lambda m: console_errors.append(m.text[:200]) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)[:200]))
    try:
        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.evaluate("document.fonts.ready")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        page.screenshot(path=str(shot), full_page=False)
        form_opened = open_form and open_applicability_form(page, width, page_key, shot)
        result = page.evaluate(MEASURE_JS, {"textColumn": TEXT_COLUMN[page_key],
                                            "tapMin": TAP_MIN_PX, "smallFont": SMALL_FONT_PX})
        if width != 390:
            result.pop("tap_targets_small")
        result["console_errors"] = console_errors[:10]
        result["title"] = page.title()
        result["form_opened"] = form_opened
        return result
    finally:
        context.close()


def flags(r: dict, width: int) -> list[str]:
    out = []
    ll = r["line_length"]
    if width >= LINE_CHECK_FROM:
        # Fulde linjer, når de findes; ellers gennemsnittet (ældre results.json).
        for kind, label in (("body", "brødtekst"), ("law", "lovtekst")):
            v = ll.get(f"{kind}_full_median", ll[f"{kind}_cpl"])
            if v is not None and not (LINE_MIN <= v <= LINE_MAX):
                out.append(f"{label} {v} tegn/linje")
    return out


def fmt(v, suffix=""):
    return "–" if v is None else f"{v}{suffix}"


def cpl_cell(m: dict, kind: str) -> str:
    ll = m["line_length"]
    avg, full = ll[f"{kind}_cpl"], ll.get(f"{kind}_full_median")
    return fmt(avg) if full is None else f"{fmt(avg)} / {full}"


def write_readme(out: Path, meta: dict, results: list[dict]) -> None:
    lines = [f"# Layout-audit: {meta['label'] or meta['base_url']}", "",
             f"- Base-URL: `{meta['base_url']}`", f"- Kørt: {meta['started']}",
             f"- Viewport-højde {HEIGHT} px; tekst ≤ {SMALL_FONT_PX} px markeres; linjelængde markeres "
             f"uden for {LINE_MIN}–{LINE_MAX} tegn ved ≥ {LINE_CHECK_FROM} px.",
             "- Tegn/linje vises som *gennemsnit / median af fulde linjer* (hver bloks sidste linje "
             "udeladt). Markeringen bruger medianen.",
             "- Trykflader: et felt i en `<label>` måles på etikettens flade.",
             "- Fuld data i `results.json`. Skærmbilleder: `<side>-<bredde>-<tema>.png` (kun viewport).", ""]
    for page_key in meta["pages"]:
        rows = [r for r in results if r["page"] == page_key]
        if not rows:
            continue
        lines += [f"## {page_key} — `{rows[0]['path']}`", "",
                  "| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | "
                  "brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            m = r["metrics"]
            if "error" in m:
                lines.append(f"| {r['width']} | {r['theme']} | FEJL: {m['error'][:80]} |||||||| |")
                continue
            main = m["main"]["width_pct"] if m["main"] else None
            col = m["text_column"]["width_pct"] if m["text_column"] else None
            taps = m.get("tap_targets_small")
            tap_txt = "–" if taps is None else f"{sum(not t['inline'] for t in taps)} ({sum(t['inline'] for t in taps)})"
            lines.append(
                f"| {r['width']} | {r['theme']} | {m['overflow_px']} | {fmt(main)} | {fmt(col)} | "
                f"{m['fonts']['small_pct']} % | {cpl_cell(m, 'body')} | "
                f"{cpl_cell(m, 'law')} | {len(m['contrast_failures'])} | {tap_txt} |")
        lines.append("")
        notes = []
        for r in rows:
            m = r["metrics"]
            if "error" in m:
                continue
            tag = f"{r['width']}/{r['theme']}"
            for o in m["overflow_offenders"][:3]:
                notes.append(f"- {tag} overløb: `{o['selector']}` {o['width']} px bred, højre kant {o['right']}")
            for f in flags(m, r["width"]):
                notes.append(f"- {tag} linjelængde: {f}")
            for c in m["contrast_failures"][:3]:
                notes.append(f"- {tag} kontrast {c['ratio']}:1 (kræver {c['required']}): `{c['selector']}` "
                             f"{c['fg']} på {c['bg']}, {c['font_px']} px — “{c['sample']}”")
            for e in m.get("console_errors", [])[:2]:
                notes.append(f"- {tag} konsolfejl: {e}")
        if notes:
            lines += ["<details><summary>Fund</summary>", ""] + notes + ["", "</details>", ""]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    widths = [int(w) for w in args.widths.split(",")]
    themes = args.themes.split(",")
    pages = args.pages.split(",")
    base = args.base_url.rstrip("/") + "/"
    meta = {"base_url": args.base_url, "label": args.label, "doc_id": args.doc_id, "widths": widths,
            "themes": themes, "pages": pages, "height": HEIGHT,
            "open_applicability_form": args.open_applicability_form,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for page_key in pages:
            path = PAGES[page_key].format(doc_id=args.doc_id)
            for width in widths:
                for theme in themes:
                    shot = out / f"{page_key}-{width}-{theme}.png"
                    # En tom side (f.eks. net::ERR_NO_BUFFER_SPACE fra Vite-dev-serveren
                    # på Windows) prøves igen, så den ikke registreres som et fund.
                    for attempt in range(1, ATTEMPTS + 1):
                        try:
                            metrics = measure(browser, base + path, width, theme, page_key, shot,
                                              open_form=args.open_applicability_form)
                        except Exception as exc:  # én fejlende side må ikke stoppe resten
                            metrics = {"error": f"{type(exc).__name__}: {exc}"}
                        if "error" not in metrics and metrics["fonts"]["total_chars"] > 0:
                            break
                        if "error" not in metrics:
                            metrics = {"error": "tom side: ingen synlig tekst",
                                       "console_errors": metrics["console_errors"]}
                        time.sleep(2 * attempt)
                    metrics["attempts"] = attempt
                    results.append({"page": page_key, "path": path, "width": width, "theme": theme,
                                    "screenshot": shot.name, "metrics": metrics})
                    status = metrics.get("error") or f"overløb {metrics['overflow_px']} px"
                    if attempt > 1:
                        status += f" (forsøg {attempt})"
                    print(f"{page_key:14} {width:5} {theme:5} {status}", flush=True)
                    if args.pause:
                        time.sleep(args.pause)
        browser.close()
    (out / "results.json").write_text(json.dumps({"meta": meta, "results": results}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    write_readme(out, meta, results)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
