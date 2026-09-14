#!/usr/bin/env python3
"""Produce safe, browser-ready transcripts from a fresh disposable WASM lab.

No external configuration, DSN, endpoint, credential or target arguments.
The rendered pages are transcript viewers, not an Alerta/Zabbix UI replica.
Screenshot the resulting HTML in a browser; preserve its bytes and this record.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from evidence_utils import code_digests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build/docs"


def screen(title: str, subtitle: str, command: str, output: str, note: str, version: str) -> str:
    return """<!doctype html><html lang="en"><meta charset="utf-8">
<title>""" + escape(title) + """ — lab transcript</title><style>
*{box-sizing:border-box}body{margin:0;padding:20px;background:#eef2f6;color:#162d3e;font:16px Arial,sans-serif}
.frame{width:1000px;padding:32px;background:#fff;border:1px solid #cbd7df;border-radius:14px}
header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #e1e7ec;padding-bottom:20px}
.brand{font-size:24px;font-weight:bold}.brand span{color:#e77917}.tag{font-size:13px;background:#fff4df;color:#734007;padding:8px 12px;border-radius:20px}
h1{font-size:28px;letter-spacing:-.5px;margin:24px 0 8px}p{color:#486073;line-height:1.5;margin:0 0 20px}
.terminal{background:#172c3c;border-radius:8px;overflow:hidden;color:#eef5fa}.bar{background:#263e4f;color:#c7d5df;font-size:13px;padding:10px 20px}
pre{font:17px/1.5 'Liberation Mono',Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:0;padding:18px 22px}
.command{color:#ffbc70;border-bottom:1px solid #354c5c;padding-bottom:16px}.result{padding-top:16px}
footer{font-size:13px;line-height:1.55;margin-top:20px;color:#486073;border-left:3px solid #e77917;padding-left:12px}
</style><main class="frame"><header><div class="brand">Zab-al-ack<span>.</span></div>
<div class="tag">""" + escape(version) + " · SYNTHETIC LAB</div></header><h1>" + escape(title) + \
        "</h1><p>" + escape(subtitle) + '</p><section class="terminal"><div class="bar">Captured transcript · selected output</div><pre class="command">' + \
        escape(command) + '</pre><pre class="result">' + escape(output) + "</pre></section><footer>" + \
        escape(note) + "</footer></main></html>\n"



def output_figure(title: str, subtitle: str, command: str, output: str, note: str, version: str) -> str:
    """Static data figure: deliberately labelled, not a browser screenshot."""
    import textwrap
    command_lines = command.splitlines()
    output_lines = output.splitlines()
    note_lines = textwrap.wrap(note, width=119)
    output_y = 242 + 25 * len(command_lines)
    terminal_bottom = output_y + 25 * len(output_lines) + 10
    height = terminal_bottom + 52 + 20 * len(note_lines)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}" role="img" aria-labelledby="title desc">',
             f'<title id="title">{escape(title)}</title><desc id="desc">Output illustration, not a UI screenshot. {escape(subtitle)} {escape(note)}</desc>',
             '<rect width="100%" height="100%" rx="12" fill="#f4f7f9"/>',
             '<text x="32" y="49" fill="#183345" font-family="Arial,sans-serif" font-weight="bold" font-size="25">Zab-al-ack<tspan fill="#b85c00">.</tspan></text>',
             f'<text x="968" y="47" text-anchor="end" fill="#78501c" font-family="Arial,sans-serif" font-size="14">{escape(version)} · SYNTHETIC LAB</text>',
             '<path d="M32 73H968" stroke="#cbd8e0"/>',
             f'<text x="32" y="116" fill="#183345" font-family="Arial,sans-serif" font-weight="bold" font-size="29">{escape(title)}</text>',
             f'<text x="32" y="147" fill="#456175" font-family="Arial,sans-serif" font-size="17">{escape(subtitle)}</text>',
             f'<rect x="32" y="169" width="936" height="{terminal_bottom-169}" rx="8" fill="#172c3c"/>',
             '<text x="54" y="197" fill="#afc6d5" font-family="Arial,sans-serif" font-size="13">OUTPUT ILLUSTRATION · OBSERVED VALUES · NOT A UI SCREENSHOT</text>']
    for y, lines, color in ((233, command_lines, '#ffbf75'), (output_y, output_lines, '#f0f6fa')):
        for line in lines:
            parts.append(f'<text x="54" y="{y}" fill="{color}" font-family="DejaVu Sans Mono,monospace" font-size="17" xml:space="preserve">{escape(line)}</text>')
            y += 25
    y = terminal_bottom + 32
    for line in note_lines:
        parts.append(f'<text x="32" y="{y}" fill="#456175" font-family="Arial,sans-serif" font-size="14">{escape(line)}</text>')
        y += 20
    return ''.join(parts) + '</svg>\n'

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated-lab", action="store_true", required=True)
    parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    # Invalidate all prior generated success before any execution.
    for name in ("observed.json", "walkthrough.json", "01-config.html", "02-action.html", "03-ledger.html", "01-config.svg", "02-action.svg", "03-ledger.svg"):
        (OUT / name).unlink(missing_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ACK_TEST_", "ZABALACK_"))
           and k != "ACK_DEPLOYMENT_E2E"}
    env.update(TZ="UTC", ZABALACK_DOCS_CAPTURE="YES_SYNTHETIC_TRANSCRIPT")
    before = code_digests(ROOT)
    config = json.loads(subprocess.check_output([sys.executable, "-m", "zabalack.cli", "--config",
        "config/production.example.json", "config-check"], cwd=ROOT, env=env, text=True, timeout=30))
    assert config["valid"] is True and config["configuration"]["external_writes_enabled"] is False
    assert config["configuration"]["cohort_count"] == config["configuration"]["instance_count"] == 0
    with (OUT / "private-run.log").open("w") as log:
        run = subprocess.run([sys.executable, "lab/pglite_exec.py", "--isolated-lab", "--", sys.executable,
            "-m", "pytest", "tests/alerta/test_docs_walkthrough.py", "-q", "--disable-warnings", "--tb=short"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    if run.returncode:
        print("Capture failed; inspect build/docs/private-run.log locally. No screenshot record produced.")
        return run.returncode
    observed = json.loads((OUT / "observed.json").read_text())
    assert before == code_digests(ROOT), "Source changed during capture"
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    limits = "Real Alerta 9.1.0 + PostgreSQL 18.3 WASM; Flask test client, synthetic JWT, injected probe, SET ROLE and mock Zabbix. G1–G8 remain open."
    selected_config = {key: config["configuration"][key] for key in (
        "enabled", "external_writes_enabled", "cohort_count", "instance_count")}
    screens = [
        ("01-config", "01 / Check the configuration", "Start with the inert production example.",
         "$ zabalack --config config/production.example.json config-check",
         json.dumps({"valid": config["valid"], "configuration": selected_config,
                     "deployment_gates": config["deployment_gates"]}, indent=2),
         "Actual CLI output; selected redacted fields. No database or Zabbix connection. Valid configuration does not establish deployment readiness."),
        ("02-action", "02 / Acknowledge one alert", "A human action enters through Alerta’s single-alert /action route.",
         "PUT /alert/" + observed["alert_id"] + "/action\n" + json.dumps(observed["request"], indent=2),
         f"HTTP {observed['http_status']}\nPersisted alert status: {observed['alert_status']}\nCapture records for this alert: {observed['capture_count']}\n\nRepeat Ack: HTTP {observed['repeat_http_status']}", limits),
        ("03-ledger", "03 / Inspect the recorded outcome", "The worker verifies, pre-reads, delivers and projects the result.",
         "SELECT zabalack.worker_call('inspect',\n  '{\"state\":\"ok\",\"limit\":1}'::jsonb);",
         json.dumps(observed["ledger"], indent=2) + "\n\nAlerta projection: zabbix_ack_sync = " + observed["projection"] +
         "\nMock pre-read event: " + ", ".join(observed["mock_pre_read_event_ids"]) +
         "\nMock acknowledgement event: " + ", ".join(observed["mock_acknowledged_event_ids"]), limits),
    ]
    pages = []
    for stem, title, subtitle, command, output, note in screens:
        path = OUT / (stem + ".html")
        path.write_text(screen(title, subtitle, command, output, note, version))
        figure = OUT / (stem + ".svg")
        figure.write_text(output_figure(title, subtitle, command, output, note, version))
        pages.append({"figure": figure.name, "figure_sha256": hashlib.sha256(figure.read_bytes()).hexdigest(), "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "command": command, "selected_output": output, "scope": note})
    record = {"version": version, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
              "code_digests": before, "status": "PASS", "scope": "Documentation transcript; not service acceptance",
              "observed": observed, "pages": pages, "external_zabbix_requests": 0, "production_gates": "NOT_VERIFIED",
              "visual_kind": "Static SVG output illustrations, not browser or application screenshots",
              "browser_capture": "NOT_RUN: this recorder creates SVG figures and HTML transcripts; browser capture is a separate step"}
    (OUT / "walkthrough.json").write_text(json.dumps(record, indent=2) + "\n")
    print("PASS: three output illustrations and browser-ready transcripts in build/docs; synthetic Alerta/WASM lab, mock Zabbix; zero external requests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
