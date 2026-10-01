#!/usr/bin/env python3
"""Regenerate the original Zabalack README overview diagrams.

The SVG source is self-contained: no remote assets, scripts or font downloads.
Run with Python 3.10+; add --png to render the accompanying previews with Inkscape.
The diagrams are explanatory overviews, not implementation state definitions.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parent
NAVY = "#1C2936"
AMBER = "#E59732"
MUTED = "#5E6974"
PAPER = "#FAF9F6"
LINE = "#D8DDDF"
WHITE = "#FFFFFF"
FONT = "Arial, Helvetica, sans-serif"

# Canonical Ack Relay / Rising Z mark from the original Zabalack brand source.
# The logo is a path, not a font glyph. Diagram labels remain accessible text.
MARK_PATH = (
    "M0.000 8.000Q0.000 0.000 8.000 0.000L296.000 0.000Q304.000 0.000 "
    "304.000 8.000L304.000 56.000Q304.000 64.000 298.343 69.657L133.657 "
    "234.343Q128.000 240.000 136.000 240.000L198.000 240.000Q206.000 "
    "240.000 211.448 234.141L306.552 131.859Q312.000 126.000 317.946 "
    "131.352L366.054 174.648Q372.000 180.000 366.556 185.862L247.444 "
    "314.138Q242.000 320.000 234.000 320.000L8.000 320.000Q0.000 320.000 "
    "0.000 312.000L0.000 264.000Q0.000 256.000 5.657 250.343L186.343 "
    "69.657Q192.000 64.000 184.000 64.000L8.000 64.000Q0.000 64.000 "
    "0.000 56.000Z"
)
MARK_VIEWBOX = (0, 0, 372, 320)


def attrs(**values: object) -> str:
    return " ".join(
        f'{key.replace("_", "-")}="{escape(str(value))}"'
        for key, value in values.items()
        if value is not None
    )


def rect(x: float, y: float, w: float, h: float, fill: str, radius: float = 0,
         stroke: str | None = None, stroke_width: float = 1.5) -> str:
    return f'<rect {attrs(x=x, y=y, width=w, height=h, rx=radius, fill=fill, stroke=stroke, stroke_width=stroke_width if stroke else None)}/>'


def path(d: str, stroke: str = NAVY, width: float = 2.5,
         fill: str = "none", **extra: object) -> str:
    return f'<path {attrs(d=d, fill=fill, stroke=stroke, stroke_width=width, stroke_linecap="round", stroke_linejoin="round", **extra)}/>'


def circle(cx: float, cy: float, r: float, fill: str, stroke: str | None = None,
           width: float = 2.5) -> str:
    return f'<circle {attrs(cx=cx, cy=cy, r=r, fill=fill, stroke=stroke, stroke_width=width if stroke else None)}/>'


def text(x: float, y: float, content: str, size: float = 22, fill: str = NAVY,
         weight: int = 400, anchor: str = "start", spacing: float | None = None) -> str:
    return f'<text {attrs(x=x, y=y, font_family=FONT, font_size=size, fill=fill, font_weight=weight, text_anchor=anchor, letter_spacing=spacing)}>{escape(content)}</text>'


def mark(x: float, y: float, size: float = 36) -> str:
    if not MARK_PATH:
        return ""
    vx, vy, vw, vh = MARK_VIEWBOX
    scale = size / max(vw, vh)
    return (
        f'<g aria-hidden="true" transform="translate({x} {y}) scale({scale}) translate({-vx} {-vy})">'
        f'<path d="{MARK_PATH}" fill="{AMBER}"/>'
        "</g>"
    )


def shell(title: str, description: str, body: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="680" '
        'viewBox="0 0 1280 680" role="img" aria-labelledby="diagram-title diagram-description">\n'
        f'<title id="diagram-title">{escape(title)}</title>\n'
        f'<desc id="diagram-description">{escape(description)}</desc>\n'
        + rect(0, 0, 1280, 680, PAPER)
        + "\n"
        + "\n".join(body)
        + "\n</svg>\n"
    )


def header(label: str, title: str, subtitle: str) -> list[str]:
    return [
        mark(64, 42, 37),
        text(117, 68, f"ZABALACK / {label}", 16, weight=700, spacing=1.6),
        rect(1045, 43, 171, 36, "#F5E5CF", 18),
        text(1130.5, 67, "ALPHA DESIGN", 15, weight=700, anchor="middle", spacing=1.1),
        text(64, 143, title, 46, weight=700),
        text(64, 183, subtitle, 23, MUTED),
    ]


def footer(label: str) -> list[str]:
    return [
        path("M64 622H1216", LINE, 1.5),
        text(64, 652, label, 18, MUTED),
        text(1216, 652, "Alpha evaluation only", 18, MUTED, weight=700, anchor="end"),
    ]


def alerta_icon(x: float, y: float) -> str:
    return "".join([
        rect(x, y, 94, 61, "none", 7, NAVY, 2.5),
        path(f"M{x+13} {y+17}H{x+63}M{x+13} {y+30}H{x+49}M{x+13} {y+43}H{x+37}", MUTED),
        circle(x+87, y+49, 17, AMBER),
        path(f"M{x+79} {y+49}l6 6 11-12", NAVY, 3),
    ])


def ledger_icon(x: float, y: float, dark: bool = False, compact: bool = False) -> str:
    ink = WHITE if dark else NAVY
    if compact:
        return "".join([
            rect(x, y, 42, 46, "none", 5, ink, 2.2),
            path(f"M{x+10} {y+13}H{x+30}M{x+10} {y+23}H{x+28}M{x+10} {y+33}H{x+20}", ink, 2.2),
            circle(x+39, y+41, 7, AMBER),
        ])
    return "".join([
        rect(x+10, y+5, 82, 58, "none", 6, "#73808C" if dark else LINE, 2.5),
        rect(x, y, 82, 58, NAVY if dark else PAPER, 6, ink, 2.5),
        path(f"M{x+13} {y+15}H{x+64}M{x+13} {y+28}H{x+56}M{x+13} {y+41}H{x+40}", ink, 2.5),
        circle(x+76, y+53, 10, AMBER),
    ])


def guard_icon(x: float, y: float) -> str:
    return "".join([
        rect(x+20, y, 53, 61, "none", 7, NAVY, 2.5),
        path(f"M{x} {y+31}H{x+13}M{x+80} {y+31}H{x+100}", NAVY, 2.5),
        path(f"M{x+93} {y+24}l7 7-7 7", NAVY, 2.5),
        circle(x+46.5, y+30.5, 16.5, "#F5E5CF"),
        path(f"M{x+38} {y+30}l6 6 12-13", NAVY, 3),
    ])


def source_icon(x: float, y: float) -> str:
    return "".join([
        rect(x, y, 95, 61, "none", 7, NAVY, 2.5),
        path(f"M{x+12} {y+15}H{x+61}", MUTED, 2.5),
        rect(x+10, y+29, 75, 22, AMBER, 5),
        circle(x+22, y+40, 3, NAVY),
        path(f"M{x+33} {y+40}H{x+56}", NAVY, 2.5),
        path(f"M{x+66} {y+39}l4 4 7-8", NAVY, 2.5),
    ])


def acknowledgement_journey() -> str:
    body = header(
        "DELIVERY OVERVIEW",
        "One acknowledgement. One careful handoff.",
        "One Alerta alert. One original Zabbix problem.",
    )
    xs = [64, 360, 656, 952]
    for index, x in enumerate(xs):
        fill = NAVY if index == 1 else ("#FFF8EC" if index == 3 else WHITE)
        body.append(rect(x, 256, 264, 244, fill, 18, None if index == 1 else LINE))
    for x in (328, 624, 920):
        body.extend([
            path(f"M{x+7} 342H{x+23}", MUTED, 2.5),
            path(f"M{x+17} 335l7 7-7 7", MUTED, 2.5),
        ])
    eyebrow = ["HUMAN ACTION", "DURABLE CAPTURE", "GUARDED DELIVERY", "ORIGINAL SOURCE"]
    titles = ["Alerta", "PostgreSQL", "Worker", "Zabbix"]
    line1 = ["One human ack", "Signed intent", "Verify, then read", "Bounded ack"]
    line2 = ["One eligible alert", "Durable record", "Check problem + scope", "Original problem only"]
    icons = [alerta_icon(88, 310), ledger_icon(384, 310, dark=True), guard_icon(680, 310), source_icon(976, 310)]
    for index, x in enumerate(xs):
        ink = WHITE if index == 1 else NAVY
        soft = "#D6DEE4" if index == 1 else MUTED
        body.extend([
            text(x+24, 287, eyebrow[index], 13.5, AMBER if index == 1 else MUTED, weight=700, spacing=1.1),
            icons[index],
            text(x+24, 406, titles[index], 29, ink, weight=700),
            text(x+24, 444, line1[index], 22, ink, weight=700),
            text(x+24, 474, line2[index], 20, soft),
        ])
    body.extend([
        path("M80 520V532H608V520", AMBER, 2.5),
        text(344, 565, "Acknowledgement + signed intent", 22, NAVY, weight=700, anchor="middle"),
        text(344, 594, "captured in one transaction", 21, MUTED, anchor="middle"),
        path("M672 520V532H1200V520", LINE, 2.5),
        text(936, 565, "Verify before source access", 22, NAVY, weight=700, anchor="middle"),
        text(936, 594, "Write only within the approved scope", 21, MUTED, anchor="middle"),
    ])
    body.extend(footer("Illustrative flow · Single-alert scope"))
    return shell(
        "One acknowledgement. One careful handoff.",
        "Illustrative Zabalack alpha-design delivery overview for isolated evaluation. "
        "An authorised human acknowledges one eligible Alerta alert. PostgreSQL captures the "
        "signed intent in the same transaction as that acknowledgement. A worker verifies "
        "the intent before source access, then checks the original Zabbix problem and approved "
        "scope before any bounded acknowledgement. This is a scoped delivery overview, "
        "not a claim of production validation or guaranteed delivery.",
        body,
    )


def outcome_icon(x: float, y: float, kind: str) -> str:
    if kind == "observed":
        return rect(x, y, 44, 44, "#F5E5CF", 10) + path(f"M{x+11} {y+22}l8 8 15-17", NAVY, 3.3)
    if kind == "eligible":
        return rect(x, y, 44, 44, AMBER, 10) + path(f"M{x+11} {y+22}H{x+32}M{x+24} {y+14}l8 8-8 8", NAVY, 3)
    return rect(x, y, 44, 44, NAVY, 10) + "".join(circle(x+dx, y+22, 2.7, AMBER) for dx in (12, 22, 32))


def delivery_outcomes() -> str:
    body = header(
        "OUTCOME HANDLING",
        "When the outcome is uncertain, preserve it.",
        "A durable record keeps intent, observations and uncertainty attached.",
    )
    body.extend([
        rect(414, 224, 452, 86, NAVY, 18),
        ledger_icon(441, 244, dark=True, compact=True),
        text(507, 260, "PostgreSQL ledger", 23, WHITE, weight=700),
        text(507, 288, "Signed intent + observations", 20, "#D6DEE4"),
        path("M640 310V343M244 373V343H1036V373M640 343V373", "#C8CFD3", 2.5),
        rect(528, 331, 224, 25, PAPER, 0),
        text(640, 349, "OBSERVED SITUATIONS", 13.5, MUTED, weight=700, anchor="middle", spacing=1.1),
    ])
    panels = [
        (64, WHITE, "observed", "SOURCE ALREADY ACKED", "Already acknowledged",
         ["Record the observation.", "No extra ack or comment."]),
        (460, "#FFF8EC", "eligible", "ALL CHECKS PERMIT", "Eligible delivery",
         ["Check original problem + scope.", "Deliver a bounded ack."]),
        (856, "#F0F3F4", "unknown", "EFFECT NOT CONFIRMED", "Unknown outcome",
         ["Investigate by source reads.", "Preserve uncertainty.", "No blind resend."]),
    ]
    for x, fill, kind, eyebrow, title, lines in panels:
        body.extend([
            rect(x, 373, 360, 222, fill, 18, LINE),
            outcome_icon(x+24, 396, kind),
            text(x+81, 423, eyebrow, 13, MUTED, weight=700, spacing=0.55),
            text(x+24, 476, title, 27, NAVY, weight=700),
        ])
        for index, line in enumerate(lines):
            body.append(text(x+24, 510+28*index, line, 21, MUTED))
    body.extend(footer("Illustrative outcomes · Single-alert scope"))
    return shell(
        "When the outcome is uncertain, preserve it.",
        "Three illustrative situations around Zabalack's PostgreSQL ledger; this is not "
        "an exhaustive implementation state diagram. An already-acknowledged source problem "
        "is observed and recorded with no extra acknowledgement or comment. An eligible delivery "
        "checks the original problem and scope and delivers only a bounded acknowledgement. "
        "An unknown remote outcome remains recorded as uncertain; reconciliation investigates "
        "by source reads and does not blindly resend a write. Alpha design for development "
        "and isolated evaluation; no production validation is implied.",
        body,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--png", action="store_true", help="Also render PNG previews with Inkscape")
    args = parser.parse_args()
    outputs = {
        "acknowledgement-journey.svg": acknowledgement_journey(),
        "delivery-outcomes.svg": delivery_outcomes(),
    }
    for name, source in outputs.items():
        destination = ROOT / name
        destination.write_text(source, encoding="utf-8")
        if args.png:
            subprocess.run([
                "inkscape", str(destination), "--export-type=png",
                "--export-filename=" + str(destination.with_suffix(".png")),
                "--export-width=1280",
            ], check=True, capture_output=True, text=True, encoding="utf-8")
        print(destination)


if __name__ == "__main__":
    main()
