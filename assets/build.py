#!/usr/bin/env python3
"""Generate the figures used by the profile README.

The README keeps all of its text in Markdown, so GitHub renders it in whatever theme
the visitor uses. The figures here are the only images, and each exists in a single
version: transparent, drawn in two inks that read on both GitHub's light and dark
pages. (README images cannot follow GitHub's theme setting: <picture> sources follow
the operating system's colour scheme, which can differ from it.)

Text inside the figures is converted to outlines, because GitHub serves README images
in a sandbox that cannot load web fonts. The fonts (SIL Open Font License) come from
Fontsource:

    mkdir -p assets/.fonts && cd assets/.fonts
    npm pack @fontsource/instrument-serif @fontsource/stix-two-text
    for f in *.tgz; do tar xzf "$f" --strip-components=2 --wildcards \
        '*/instrument-serif-latin-400-*.woff2' '*/stix-two-text-latin-400-*.woff2' \
        '*/stix-two-text-greek-400-italic.woff2' 2>/dev/null; done

Then, from the repository root:

    pip install numpy fonttools brotli uharfbuzz pillow
    python3 assets/build.py

If assets/portrait.jpg exists, it is embedded as the single observed data point in the
header figure; otherwise a plain marker takes its place.
"""

from __future__ import annotations

import base64
import io
import math
import os
import re
from pathlib import Path

import numpy as np
import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "svg"
FONT_DIR = Path(os.environ.get("FONT_DIR", ROOT / ".fonts"))
PORTRAIT = ROOT / "portrait.jpg"

# Both inks keep at least 3.5:1 contrast against GitHub's light (#ffffff) and
# dark (#0d1117) pages. `ink` is GitHub's own muted text colour.
PALETTE = dict(accent="#d9630f", ink="#7d8590")


def num(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


# --------------------------------------------------------------------------- type


class Face:
    """A font that shapes text with HarfBuzz and hands out glyph outlines."""

    def __init__(self, key: str, filename: str):
        self.key = key
        tt = TTFont(FONT_DIR / filename)
        tt.flavor = None  # woff2 -> sfnt, so HarfBuzz can read it
        buf = io.BytesIO()
        tt.save(buf)
        data = buf.getvalue()
        self.tt = TTFont(io.BytesIO(data))
        self.glyphs = self.tt.getGlyphSet()
        self.order = self.tt.getGlyphOrder()
        self.upm = self.tt["head"].unitsPerEm
        self.hb = hb.Font(hb.Face(hb.Blob(data)))

    def shape(self, s: str, size: float):
        buf = hb.Buffer()
        buf.add_str(s)
        buf.guess_segment_properties()
        hb.shape(self.hb, buf, {"kern": True, "liga": True})
        scale = size / self.upm
        out, x = [], 0.0
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            out.append((info.codepoint, x + pos.x_offset * scale, pos.y_offset * scale))
            x += pos.x_advance * scale
        return out, x

    def width(self, s: str, size: float) -> float:
        return self.shape(s, size)[1]

    def outline(self, gid: int) -> str:
        pen = SVGPathPen(self.glyphs, ntos=lambda v: str(round(v)))
        self.glyphs[self.order[gid]].draw(pen)
        return pen.getCommands()


SERIF = Face("s", "instrument-serif-latin-400-normal.woff2")
SERIF_I = Face("si", "instrument-serif-latin-400-italic.woff2")
MATH = Face("x", "stix-two-text-latin-400-normal.woff2")
MATH_I = Face("xi", "stix-two-text-latin-400-italic.woff2")
GREEK_I = Face("gi", "stix-two-text-greek-400-italic.woff2")


class Doc:
    """One SVG figure. Glyphs are defined once and placed with <use>, which keeps files small."""

    def __init__(self, width: int, height: int, label: str = ""):
        self.w, self.h, self.label = width, height, label
        self.defs: dict[str, str] = {}
        self.body: list[str] = []
        self.style = ""

    def add(self, *parts: str):
        self.body.extend(parts)

    def glyph_ref(self, face: Face, gid: int) -> str | None:
        ref = f"{face.key}{gid}"
        if ref not in self.defs:
            d = face.outline(gid)
            if not d:
                return None  # spaces
            self.defs[ref] = f'<path id="{ref}" d="{d}"/>'
        return ref

    def run(self, face: Face, s: str, x: float, y: float, size: float) -> tuple[str, float]:
        glyphs, w = face.shape(s, size)
        k = f"{size / face.upm:.5g}"
        uses = []
        for gid, gx, gy in glyphs:
            ref = self.glyph_ref(face, gid)
            if ref:
                uses.append(f'<use href="#{ref}" transform="matrix({k} 0 0 -{k} {num(x + gx)} {num(y - gy)})"/>')
        return "".join(uses), w

    def text(self, face, s, x, y, size, fill, anchor="start", cls=""):
        return self.rich([(face, s)], x, y, size, fill, anchor, cls)

    def rich(self, segments, x, y, size, fill, anchor="start", cls=""):
        """Set a line made of (face, text) runs and (shape, None) symbols drawn by hand."""

        def seg_width(face, s):
            return SHAPES[face][0] * size if isinstance(face, str) else face.width(s, size)

        total = sum(seg_width(f, s) for f, s in segments)
        x -= {"start": 0, "middle": total / 2, "end": total}[anchor]
        out = []
        for face, s in segments:
            if isinstance(face, str):
                advance, draw = SHAPES[face]
                out.append(draw(x, y, size, fill))
                x += advance * size
            else:
                part, w = self.run(face, s, x, y, size)
                out.append(part)
                x += w
        cls = f' class="{cls}"' if cls else ""
        return f'<g fill="{fill}"{cls}>{"".join(out)}</g>'

    def render(self) -> str:
        style = f"<style>{self.style}</style>" if self.style else ""
        defs = f'<defs>{"".join(self.defs.values())}</defs>' if self.defs else ""
        aria = f'role="img" aria-label="{self.label}"><title>{self.label}</title>' if self.label else 'aria-hidden="true">'
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" {aria}{style}{defs}{"".join(self.body)}</svg>\n'
        )


def _approx(x, y, size, color):
    w, a, sw = size * 0.52, size * 0.05, size * 0.06
    x0 = x + size * 0.12
    out = []
    for dy in (-0.4, -0.2):
        yy = y + dy * size
        out.append(
            f"M{num(x0)} {num(yy + a)}C{num(x0 + w * 0.3)} {num(yy - 3 * a)} {num(x0 + w * 0.7)} "
            f"{num(yy + 3 * a)} {num(x0 + w)} {num(yy - a)}"
        )
    return f'<path d="{"".join(out)}" fill="none" stroke="{color}" stroke-width="{num(sw)}" stroke-linecap="round"/>'


def _arrow_right(x, y, size, color):
    yy, x0, x1, h = y - size * 0.3, x + size * 0.12, x + size * 0.82, size * 0.18
    return (
        f'<path d="M{num(x0)} {num(yy)}H{num(x1)}M{num(x1 - h)} {num(yy - h)}L{num(x1)} {num(yy)}'
        f'L{num(x1 - h)} {num(yy + h)}" fill="none" stroke="{color}" stroke-width="{num(size * 0.065)}" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )


SHAPES = {"approx": (0.76, _approx), "arrow": (0.94, _arrow_right)}


def write(name: str, doc: Doc):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(doc.render(), encoding="utf-8")


# ------------------------------------------------------------------------- header


def gp_posterior():
    """A Gaussian-process posterior that stays low under the name and rises to the portrait."""

    def truth(x):
        return 0.16 + 0.05 * np.sin(14 * x) + 0.74 * np.exp(-(((x - 0.84) / 0.13) ** 2))

    obs_x = np.array([0.02, 0.09, 0.16, 0.25, 0.33, 0.41, 0.49, 0.57, 0.84])
    jitter = np.array([0.012, -0.01, 0.008, -0.006, 0.01, -0.012, 0.006, -0.004, 0.0])
    obs_y = truth(obs_x) + jitter

    ell, sf, noise, prior = 0.075, 0.24, 2e-5, 0.16

    def k(a, b):
        return sf**2 * np.exp(-((a[:, None] - b[None, :]) ** 2) / (2 * ell**2))

    xs = np.linspace(0, 1, 201)
    kxx = k(obs_x, obs_x) + noise * np.eye(len(obs_x))
    ks = k(xs, obs_x)
    mu = prior + ks @ np.linalg.solve(kxx, obs_y - prior)
    cov = k(xs, xs) - ks @ np.linalg.solve(kxx, ks.T)
    std = np.sqrt(np.clip(np.diag(cov), 0, None))

    w, v = np.linalg.eigh((cov + cov.T) / 2)
    root = v * np.sqrt(np.clip(w, 0, None))
    rng = np.random.default_rng(7)
    samples = mu + (root @ rng.standard_normal((len(xs), 4))).T
    return xs, mu, std, samples, obs_x, obs_y


def portrait_data_uri(size: int) -> str | None:
    if not PORTRAIT.exists():
        return None
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(PORTRAIT)).convert("RGB")
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, max(0, int((h - side) * 0.25))
    img = img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=84, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


HEADER_LABEL = "Hi, I’m Philipp Oberdiek."


def header(t, portrait):
    W, H = 1280, 520
    X0, X1 = 40, 1240
    doc = Doc(W, H, HEADER_LABEL)
    xs, mu, std, samples, obs_x, obs_y = gp_posterior()
    a, ink = t["accent"], t["ink"]

    def px(x):
        return X0 + x * (X1 - X0)

    def py(f):
        return 450 - f * 250

    def line(ys):
        return "M" + "L".join(f"{num(px(x))} {num(py(y))}" for x, y in zip(xs, ys))

    def band(k):
        up = [f"{num(px(x))} {num(py(y))}" for x, y in zip(xs, mu + k * std)]
        lo = [f"{num(px(x))} {num(py(y))}" for x, y in zip(xs[::-1], (mu - k * std)[::-1])]
        return "M" + "L".join(up + lo) + "Z"

    # name
    doc.add(doc.text(SERIF_I, "Hi, I’m", X0, 74, 46, ink, cls="rise"))
    doc.add(doc.text(SERIF, "Philipp Oberdiek", X0 - 4, 190, 128, a, cls="rise"))

    # plot
    doc.add('<clipPath id="plot"><rect x="0" y="96" width="1280" height="372"/></clipPath>')
    doc.add('<g clip-path="url(#plot)">')
    doc.add(f'<path class="band" d="{band(2)}" fill="{a}" fill-opacity=".12"/>')
    doc.add(f'<path class="band" d="{band(1)}" fill="{a}" fill-opacity=".2"/>')
    for i, phase in enumerate((0, 2)):
        order = [samples[(phase + j) % 4] for j in range(5)]
        values = ";".join(line(s) for s in order)
        doc.add(
            f'<path class="sample" d="{line(order[0])}" fill="none" stroke="{ink}" '
            f'stroke-opacity=".5" stroke-width="1.2">'
            f'<animate attributeName="d" dur="{18 + 5 * i}s" repeatCount="indefinite" '
            f'calcMode="spline" keyTimes="0;.25;.5;.75;1" '
            f'keySplines=".45 0 .55 1;.45 0 .55 1;.45 0 .55 1;.45 0 .55 1" values="{values}"/></path>'
        )
    doc.add(
        f'<path class="mean" pathLength="1" d="{line(mu)}" fill="none" stroke="{a}" '
        f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    doc.add("</g>")

    for i, (x, y) in enumerate(zip(obs_x[:-1], obs_y[:-1])):
        doc.add(
            f'<circle class="pt" style="animation-delay:{0.9 + 0.08 * i:.2f}s" cx="{num(px(x))}" '
            f'cy="{num(py(y))}" r="5.5" fill="{ink}"/>'
        )

    # the one observation that matters
    cx, cy, r = px(obs_x[-1]), py(obs_y[-1]), 44
    doc.add('<g class="me">')
    if portrait:
        doc.add(f'<clipPath id="face"><circle cx="{num(cx)}" cy="{num(cy)}" r="{r}"/></clipPath>')
        doc.add(
            f'<image href="{portrait}" x="{num(cx - r)}" y="{num(cy - r)}" width="{2 * r}" '
            f'height="{2 * r}" clip-path="url(#face)" preserveAspectRatio="xMidYMid slice"/>'
        )
        doc.add(f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{r + 5}" fill="none" stroke="{a}" stroke-width="2.5"/>')
    else:
        doc.add(f'<circle cx="{num(cx)}" cy="{num(cy)}" r="7.5" fill="{a}"/>')
        doc.add(f'<circle cx="{num(cx)}" cy="{num(cy)}" r="16" fill="none" stroke="{a}" stroke-width="2.5"/>')
    doc.add(
        doc.rich(
            [(MATH_I, "n"), (MATH, " = 1,  "), (GREEK_I, "σ"), (MATH, " "), ("approx", None), (MATH, " 0")],
            cx, cy + r + 40, 26, ink, anchor="middle",
        )
    )
    doc.add("</g>")

    # axis
    doc.add(f'<path d="M{X0} 468H{X1}" stroke="{ink}" stroke-width="1.2"/>')
    ticks = "".join(f"M{num(X0 + i * (X1 - X0) / 12)} 468v8" for i in range(13))
    doc.add(f'<path d="{ticks}" stroke="{ink}" stroke-width="1.2"/>')
    doc.add(doc.rich([(SERIF_I, "time "), ("arrow", None)], X1, 506, 26, ink, anchor="end"))

    doc.style = (
        "@keyframes draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}"
        "@keyframes fade{from{opacity:0}to{opacity:1}}"
        "@keyframes pop{from{opacity:0;transform:scale(.2)}to{opacity:1;transform:scale(1)}}"
        "@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}"
        ".mean{stroke-dasharray:1;animation:draw 2.4s cubic-bezier(.65,0,.35,1) both .3s}"
        ".band{animation:fade 1.6s ease-out both 1.1s}"
        ".sample{animation:fade 2s ease-out both 2.2s}"
        ".pt{transform-box:fill-box;transform-origin:center;animation:pop .5s cubic-bezier(.3,1.6,.6,1) both}"
        ".me{transform-box:fill-box;transform-origin:center;animation:pop .7s cubic-bezier(.3,1.4,.6,1) both 2s}"
        ".rise{animation:rise .9s cubic-bezier(.2,.7,.2,1) both}"
        "@media (prefers-reduced-motion:reduce){*{animation:none!important}.sample{display:none}}"
    )
    return doc


# -------------------------------------------------------------- illustrations
# Each one draws into a 140 x 140 box.


def ill_uqgan(t):
    rng = np.random.default_rng(3)
    a, ink = t["accent"], t["ink"]
    out = []
    for cx, cy in ((42, 92), (100, 46)):
        out.append(
            f'<ellipse cx="{cx}" cy="{cy}" rx="30" ry="22" transform="rotate(-30 {cx} {cy})" '
            f'fill="none" stroke="{ink}" stroke-opacity=".52" stroke-dasharray="2 3"/>'
        )
        out.append(
            f'<ellipse cx="{cx}" cy="{cy}" rx="17" ry="12" transform="rotate(-30 {cx} {cy})" '
            f'fill="none" stroke="{ink}" stroke-opacity=".75"/>'
        )
        for _ in range(9):
            x, y = cx + rng.normal(0, 8), cy + rng.normal(0, 6)
            out.append(f'<circle cx="{num(x)}" cy="{num(y)}" r="2.6" fill="{ink}"/>')
    out.append(f'<path d="M14 14C60 40 84 90 128 128" fill="none" stroke="{a}" stroke-width="2.2"/>')
    out.append(
        f'<path d="M6 30C52 56 70 104 108 136M24 4C70 28 98 74 138 112" fill="none" stroke="{a}" '
        f'stroke-opacity=".6" stroke-dasharray="3 4"/>'
    )
    return "".join(out)


def ill_ood(t):
    rng = np.random.default_rng(5)
    a, ink = t["accent"], t["ink"]
    out = [f'<path d="M0 {14 * i}H140" stroke="{ink}" stroke-opacity=".12"/>' for i in range(1, 10)]
    out += [f'<path d="M{14 * i} 0V140" stroke="{ink}" stroke-opacity=".12"/>' for i in range(1, 10)]
    out.append(
        f'<ellipse cx="50" cy="88" rx="38" ry="30" fill="none" stroke="{ink}" stroke-opacity=".6" stroke-dasharray="2 3"/>'
    )
    for _ in range(22):
        x, y = 50 + rng.normal(0, 12), 88 + rng.normal(0, 9)
        out.append(f'<circle cx="{num(x)}" cy="{num(y)}" r="2.6" fill="{ink}"/>')
    out.append(f'<circle cx="112" cy="30" r="17" fill="none" stroke="{a}" stroke-width="1.8" stroke-dasharray="4 3"/>')
    out.append(f'<circle cx="112" cy="30" r="5" fill="{a}"/>')
    return "".join(out)


def ill_gradients(t):
    a, ink = t["accent"], t["ink"]
    out = []
    for i in range(5):
        for j in range(5):
            x, y = 14 + i * 28, 14 + j * 28
            dx, dy = -(y - 70), (x - 70)
            mag = math.hypot(dx, dy)
            if not mag:
                out.append(f'<circle cx="{x}" cy="{y}" r="2" fill="{ink}" fill-opacity="1"/>')
                continue
            length = 5 + 13 * mag / 80
            ux, uy = dx / mag, dy / mag
            x0, y0 = x - ux * length / 2, y - uy * length / 2
            x1, y1 = x + ux * length / 2, y + uy * length / 2
            hx, hy = -ux * 5, -uy * 5
            head = (
                f"M{num(x1 + hx - uy * 3.5)} {num(y1 + hy + ux * 3.5)}L{num(x1)} {num(y1)}"
                f"L{num(x1 + hx + uy * 3.5)} {num(y1 + hy - ux * 3.5)}"
            )
            color, op = (a, "1") if (i, j) == (3, 1) else (ink, ".7")
            out.append(
                f'<path d="M{num(x0)} {num(y0)}L{num(x1)} {num(y1)}{head}" fill="none" stroke="{color}" '
                f'stroke-opacity="{op}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
            )
    return "".join(out)


def ill_annotation(t):
    a, ink = t["accent"], t["ink"]
    out = [
        f'<rect x="58" y="4" width="40" height="132" fill="{a}" fill-opacity=".21"/>',
        f'<path d="M58 4V136M98 4V136" stroke="{a}" stroke-width="1.8"/>',
    ]
    xs = np.linspace(0, 140, 70)
    for k, base in enumerate((28, 70, 112)):
        ys = base + 9 * np.sin(xs / (9 + 3 * k) + k) + 5 * np.sin(xs / 3.1 + 2 * k)
        d = "M" + "L".join(f"{num(x)} {num(y)}" for x, y in zip(xs, ys))
        out.append(f'<path d="{d}" fill="none" stroke="{ink}" stroke-opacity="1" stroke-width="1.5"/>')
    return "".join(out)


def ill_sensor(t):
    a, ink = t["accent"], t["ink"]
    out = [f'<path d="M0 64H140" stroke="{ink}" stroke-opacity=".9" stroke-width="1.5"/>']
    out += [f'<path d="M{8 + 14 * i} 70l-8 10" stroke="{ink}" stroke-opacity=".38"/>' for i in range(10)]
    out.append(f'<rect x="56" y="8" width="28" height="122" rx="5" fill="none" stroke="{ink}" stroke-width="1.8"/>')
    out.append(f'<path d="M70 130V36" stroke="{ink}" stroke-opacity=".45"/>')
    out.append(f'<rect x="61" y="16" width="18" height="16" rx="2" fill="{ink}"/>')
    out.append(f'<path d="M32 98c0 0-7 8-7 12a7 7 0 0 0 14 0c0-4-7-12-7-12z" fill="{a}"/>')
    out.append(f'<path d="M108 84c0 0-5 6-5 9a5 5 0 0 0 10 0c0-3-5-9-5-9z" fill="{a}" fill-opacity=".9"/>')
    out.append(
        f'<path d="M96 24a14 14 0 0 1 0 20M104 18a24 24 0 0 1 0 32" fill="none" stroke="{a}" '
        f'stroke-width="1.8" stroke-linecap="round"/>'
    )
    return "".join(out)


def ill_crosshair(t):
    a, ink = t["accent"], t["ink"]
    out = [
        f'<rect x="22" y="6" width="96" height="128" rx="3" fill="none" stroke="{ink}" '
        f'stroke-opacity=".83" stroke-width="1.5"/>'
    ]
    for i, w in enumerate((70, 62, 74, 40, 0, 70, 66, 52)):
        if w:
            out.append(
                f'<path d="M34 {22 + 13 * i}h{w}" stroke="{ink}" stroke-opacity=".45" stroke-width="3" stroke-linecap="round"/>'
            )
    out.append(
        f'<rect x="46" y="64" width="50" height="34" fill="{a}" fill-opacity=".21" stroke="{a}" '
        f'stroke-width="1.5" stroke-dasharray="4 3"/>'
    )
    out.append(f'<path d="M0 98H140M96 0V140" stroke="{a}" stroke-width="1.2" stroke-dasharray="1 3"/>')
    out.append(f'<circle cx="96" cy="98" r="3.5" fill="{a}"/>')
    return "".join(out)


ILLUSTRATIONS = {
    "uqgan": ill_uqgan,
    "oodretrieval": ill_ood,
    "gradient-metrics": ill_gradients,
    "annotation-tool": ill_annotation,
    "soil-sensor": ill_sensor,
    "zotero-crosshair": ill_crosshair,
}


def illustration(t, draw) -> Doc:
    doc = Doc(160, 160)
    doc.add(f'<g transform="translate(10 10)">{draw(t)}</g>')
    return doc


def main():
    for old in OUT.glob("*.svg"):
        old.unlink()
    write("header.svg", header(PALETTE, portrait_data_uri(176)))
    for slug, draw in ILLUSTRATIONS.items():
        write(f"{slug}.svg", illustration(PALETTE, draw))
    print(f"wrote {len(list(OUT.glob('*.svg')))} files to {OUT}")


if __name__ == "__main__":
    main()
