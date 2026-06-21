#!/usr/bin/env python3
"""
parse_vendor_docs.py — turn downloaded vendor command-reference docs into
intermediate JSON records matching the multivendor-cli-configurator schema:

    {"vendor","os","role","cat","title","cmd","desc"}

This is a SCAFFOLD. Each vendor doc is laid out differently, so the per-source
`style` extractors below are starting points — refine the regexes against the
real text dumps. The goal is to emit clean per-source JSON that your existing
`merge_dcn_corpus.py` / `clean_titles.py` / `audit_data_quality.py` then process.

Pipeline fit (from the repo README):
    scripts/sources/<id>.pdf  ->  parse_vendor_docs.py  ->  out/<id>.json
    -> merge_dcn_corpus.py -> clean_titles.py -> audit_data_quality.py -> commands.json

Text extraction order: `pdftotext -layout` (poppler) if present, else pypdf if
installed. Install one:  brew install poppler   OR   pip install pypdf

Usage:
    python3 parse_vendor_docs.py --all
    python3 parse_vendor_docs.py --source cisco_ios_mcl
    python3 parse_vendor_docs.py --source extreme_exos_cref --limit 50   # preview
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _resolve_manifest() -> Path:
    """Find candidate_sources.json whether it sits next to this script or in
    the sibling docs/ folder (the repo keeps the manifest under docs/)."""
    candidates = [
        HERE / "candidate_sources.json",
        HERE.parent / "docs" / "candidate_sources.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    searched = "\n  ".join(str(c) for c in candidates)
    sys.exit(f"candidate_sources.json not found. Looked in:\n  {searched}")


MANIFEST = _resolve_manifest()
SRCDIR = HERE / "sources"
OUTDIR = HERE / "out"

# ── category heuristics (chapter / command-name → cat) ────────────────────────
CAT_RULES = [
    (r"\bvlan\b|switchport|trunk|access[- ]?port", "VLAN"),
    (r"spanning.?tree|\bstp\b|rstp|mstp|rpvst",     "STP"),
    (r"\bospf\b",                                   "OSPF"),
    (r"\beigrp\b",                                  "EIGRP"),
    (r"\bbgp\b",                                    "BGP"),
    (r"redistribut|route-map|prefix-list|\brib\b",  "Routing"),
    (r"\bacl\b|access-list|firewall|zone|policy|aaa|tacacs|radius", "Security"),
    (r"\bnat\b|\bvpn\b|ipsec|ike",                  "Security"),
    (r"\bboot\b|bootp|archive|nvram|config-register|\bimage\b|upgrade|"
     r"reload|\balias\b|activation|baud|tar file|secure file system", "System"),
    (r"interface|\bmtu\b|speed|duplex|lacp|port-channel|eth-trunk", "Interfaces"),
    (r"\bsnmp\b|\bntp\b|logging|syslog|hostname|banner|clock|aaa",  "System"),
    (r"\bshow\b|debug|ping|traceroute|monitor|info from state",     "Troubleshooting"),
]


def categorize(*texts: str) -> str:
    blob = " ".join(t.lower() for t in texts if t)
    for pat, cat in CAT_RULES:
        if re.search(pat, blob):
            return cat
    return "Misc"


# ── text extraction ───────────────────────────────────────────────────────────
def extract_text(path: Path) -> str:
    if path.suffix.lower() != ".pdf":
        return path.read_text(errors="ignore")
    if shutil.which("pdftotext"):
        # -layout keeps the column structure command refs rely on
        return subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True, check=True,
        ).stdout
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(path))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)
    except ImportError:
        sys.exit("No text extractor. Install poppler (pdftotext) or `pip install pypdf`.")


# ── per-source style extractors ───────────────────────────────────────────────
# Each returns a list of (title, cmd, desc) tuples. cat/role/vendor/os are added
# by the caller from the manifest. Keep cmd = canonical syntax line; title = the
# command name; desc = first sentence(s) of the description.

# Cisco command-ref definition sentence:
#   "To <do X>, use the **<command>** command in <mode> mode."
# The command name is bold-delimited (**...**) in both the HTML render and the
# pdftotext output of recent Cisco refs. Anchoring on the bold pair avoids the
# greedy-match trap and naturally skips "use the **no** form of this command".
# No DOTALL: each definition is a single line, so '.' must not cross newlines
# (otherwise desc bleeds into the previous entry's trailing "no form" clause).
# The negative lookahead stops desc from swallowing an earlier "use the ..."
# clause on the same line. The command name is matched either bold-delimited
# (**name** — HTML/markdown render) OR plain (pdftotext render of the PDF), so
# the same extractor works on both the web page and the downloaded PDF.
_CISCO_DEF = re.compile(
    r"[Tt]o\s+(?P<desc>(?:(?!\buse the\b).)+?),\s+use\s+the\s+"
    r"(?:\*\*(?P<cmd_b>[^*\n]{2,80}?)\*\*|(?P<cmd_p>[a-z][a-z0-9\- ]{1,79}?))\s+"
    r"command(?:\s+in\s+(?P<mode>[\w\- ]+?\s+mode))?",
)


def style_cisco_cref(text: str) -> list[tuple[str, str, str]]:
    """Cisco command refs: extract each command's definition sentence.

    Returns (title, cmd, desc). title == cmd == the bold command name; desc is
    the 'To ...' purpose clause plus the config mode when present.
    """
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for m in _CISCO_DEF.finditer(text):
        cmd = re.sub(r"\s+", " ", m.group("cmd_b") or m.group("cmd_p") or "").strip()
        low = cmd.lower()
        if not cmd or low == "no" or low.startswith("no ") or "form of this" in low:
            continue
        if cmd in seen:
            continue
        seen.add(cmd)
        desc = re.sub(r"\s+", " ", m.group("desc")).strip()
        desc = (desc[:1].upper() + desc[1:]) if desc else ""
        mode = re.sub(r"\s+", " ", m.group("mode") or "").strip()
        if mode:
            desc = f"{desc} (in {mode})."
        elif desc and not desc.endswith("."):
            desc += "."
        out.append((cmd, cmd, desc[:300]))
    return out


def style_exos_cref(text: str):
    """ExtremeXOS: 'Syntax\\n<cmd>' then 'Description\\n<text>'."""
    out = []
    for m in re.finditer(
        r"Syntax\s*\n\s*(.+?)\n.*?Description\s*\n\s*(.+?)(?:\n\s*\n|Example)",
        text, re.S,
    ):
        cmd = re.sub(r"\s+", " ", m.group(1)).strip()
        desc = re.sub(r"\s+", " ", m.group(2)).strip()
        title = cmd.split("{")[0].split("<")[0].strip()
        if 2 <= len(cmd) <= 200:
            out.append((title, cmd, desc[:300]))
    return out


# Cisco IOS Master Command List is an *index*: each line is a command name followed
# (single-space separated) by one-or-more uppercase reference-book codes, e.g.
#   "aaa authentication login SEC"
#   "aaa accounting nested SEC, VPD"
#   "100rel inbound SBCD"
# Book codes are pure uppercase tokens (IPV6 has a digit) — so we strip trailing
# book-code tokens from the right; whatever remains is the command. Command tokens
# are lowercase / mixed-case-in-parens ("(WebVPN)", "(IKEv2 profile)") and never
# pure-uppercase, so they don't get mistaken for a book code.
_MCL_BOOK = re.compile(r"^[A-Z][A-Z0-9]{1,5},?$")
# Book code → category, derived from the MCL legend (CODE = "Cisco IOS <topic>
# Command Reference"). Covers every book code that appears in the index so the
# book-code fallback keeps cat:Misc near zero.
_MCL_BOOK_CAT = {
    "SEC": "Security", "VR": "Protocols", "DB": "Troubleshooting", "CBL": "Interfaces",
    "IR": "Interfaces", "SBCD": "Protocols", "SBCU": "Protocols", "IPV6": "Protocols",
    "CF": "System", "QOS": "QoS", "DIA": "Interfaces", "IBM": "Protocols",
    "IAD": "Routing", "MP": "MPLS", "WAN": "Protocols", "IMC": "Multicast",
    "BGP": "BGP", "SLA": "Troubleshooting", "MWG": "Wireless", "ATM": "Interfaces",
    "SNMP": "SNMP", "CE": "Interfaces", "LSW": "VLAN", "IMO": "Protocols",
    "ISW": "Routing", "IPX": "Protocols", "PFR": "Routing", "ISG": "Protocols",
    "SLB": "Protocols", "BBA": "Interfaces", "MM": "Troubleshooting", "SSG": "Protocols",
    "LISP": "Routing", "VPD": "VPN", "TSV": "System", "IAP": "Protocols",
    "OSPF": "OSPF", "EEM": "Automation", "ISO": "Protocols", "OER": "Routing",
    "FNF": "Troubleshooting", "ATK": "Protocols", "EIGRP": "EIGRP", "MWP": "Wireless",
    "HA": "HA", "IRI": "Routing", "BR": "VLAN", "IRS": "ISIS", "NF": "Troubleshooting",
    "FHP": "HA", "BSM": "System", "WL": "Wireless", "CNS": "Automation",
    "MTR": "Routing", "DEC": "Protocols", "SAF": "Protocols", "HTTPS": "System",
    "CSA": "System", "RIP": "RIP", "ESM": "Logging", "WSMA": "Automation",
    "ERM": "System", "RMON": "Troubleshooting", "CDP": "Protocols", "EPC": "Troubleshooting",
    "EVN": "Routing", "MWR": "Wireless", "XMLPI": "Automation", "MSP": "Protocols",
    "NM": "System", "VS": "VLAN", "MUL": "Multicast", "MPLS": "MPLS", "FM": "System",
    "ANCP": "Protocols", "AN": "System", "EM": "System",
}


def style_cisco_mcl(text: str):
    """Cisco IOS Master Command List index → (title, cmd, desc, cat) per command."""
    out, seen = [], set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or " through " in line:          # blanks + range aggregator rows
            continue
        toks = line.split()
        # peel trailing book-code tokens off the right
        books = []
        while toks and _MCL_BOOK.match(toks[-1]):
            books.insert(0, toks.pop().rstrip(","))
        if not books or not toks:                    # need both a command and >=1 book
            continue
        cmd = " ".join(toks)
        if not re.match(r"^[a-z0-9]", cmd) or len(cmd) < 3:   # commands start lower/digit
            continue
        if cmd in seen or cmd.lower().startswith(("cisco ios", "page ", "1, a through", "table ")):
            continue
        seen.add(cmd)
        booklist = ", ".join(books)
        desc = f"Cisco IOS command (ref: {booklist})."
        out.append((cmd, cmd, desc, _MCL_BOOK_CAT.get(books[0])))
    return out


# Nokia SR OS "CLI Usage" guide — clean command/description pairs in two reliable
# shapes: help-globals dash form ("back   - Go back a level ...") and console-table
# form ("ping   Verifies the reachability of a remote host.   100").
_SROS_DASH = re.compile(r"^\s+(?P<cmd>[a-z][a-z0-9\-]+(?: [a-z][a-z0-9\-]+)?)\s+[-+]\s+(?P<desc>[A-Za-z].+?)\s*$")
_SROS_TABLE = re.compile(r"^\s+(?P<cmd>[a-z][a-z0-9\-]+(?: [a-z][a-z0-9\-]+)?)\s{2,}(?P<desc>[A-Z].+?)(?:\s{2,}\d{1,4})?\s*$")


def style_nokia_sros(text: str):
    """Nokia SR OS (os=sros) — dash-help and console-table command rows."""
    out, seen = [], set()
    for line in text.splitlines():
        m = _SROS_DASH.match(line) or _SROS_TABLE.match(line)
        if not m:
            continue
        cmd = re.sub(r"\s+", " ", m.group("cmd")).strip()
        desc = re.sub(r"\s+", " ", m.group("desc")).strip()
        if cmd in seen or len(cmd) < 2 or len(desc) < 8 or cmd.endswith("."):
            continue
        if cmd in {"for example", "the following", "this is", "page"}:
            continue
        seen.add(cmd)
        if not desc.endswith("."):
            desc += "."
        out.append((cmd, cmd, desc[:300]))
    return out


def style_srlinux_decl(text: str):
    """Nokia SR Linux declarative CLI: capture `set / ...` paths and `show`/info."""
    out = []
    for m in re.finditer(r"^\s*((?:set|info|show)\s+[^\n]{3,160})$", text, re.M):
        cmd = re.sub(r"\s+", " ", m.group(1)).strip()
        title = cmd.split()[0] + " " + (cmd.split()[1] if len(cmd.split()) > 1 else "")
        out.append((title.strip(), cmd, ""))
    seen, uniq = set(), []
    for t in out:
        if t[1] not in seen:
            seen.add(t[1]); uniq.append(t)
    return uniq


STYLES = {
    "cisco_ios_mcl": style_cisco_mcl,
    "cisco_ios_lanswitch_cref": style_cisco_cref,
    "cisco_ios_security_cref": style_cisco_cref,
    "cisco_ios_fundamentals_cref": style_cisco_cref,
    "extreme_exos_cref": style_exos_cref,
    "nokia_sros_7750": style_nokia_sros,
    "nokia_srlinux_cli_plugin": style_srlinux_decl,
    "nokia_srlinux_config_basics": style_srlinux_decl,
}


def parse_source(src: dict, limit: int | None = None) -> list[dict]:
    sid = src["id"]
    style = STYLES.get(sid)
    if style is None:
        print(f"  ! no style extractor for {sid}; skipping"); return []
    path = next(iter(SRCDIR.glob(f"{sid}.*")), None)
    if path is None:
        print(f"  ! missing source file {SRCDIR}/{sid}.* — run fetch_sources.sh {sid}")
        return []
    text = extract_text(path)
    triples = style(text)
    records = []
    for row in triples:
        title, cmd, desc = row[0], row[1], row[2]
        cat_hint = row[3] if len(row) > 3 else None       # optional style-supplied category
        cat = categorize(title, cmd, desc)
        if cat == "Misc" and cat_hint:                    # fall back to the style's hint
            cat = cat_hint
        records.append({
            "vendor": src["vendor"],
            "os": src["os"],
            "role": src.get("default_role", "router"),
            "cat": cat,
            "title": title,
            "cmd": cmd,
            "desc": desc,
        })
    if limit:
        records = records[:limit]
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="single source id from candidate_sources.json")
    ap.add_argument("--all", action="store_true", help="parse every source")
    ap.add_argument("--limit", type=int, help="cap records (preview)")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    sources = manifest["sources"]
    if args.source:
        sources = [s for s in sources if s["id"] == args.source]
        if not sources:
            sys.exit(f"unknown source id: {args.source}")
    elif not args.all:
        sys.exit("pass --all or --source <id>")

    OUTDIR.mkdir(exist_ok=True)
    grand = 0
    for src in sources:
        print(f"» {src['id']}  ({src['vendor']}/{src['os']})")
        recs = parse_source(src, args.limit)
        outpath = OUTDIR / f"{src['id']}.json"
        outpath.write_text(json.dumps(recs, indent=2))
        print(f"  {len(recs)} records -> {outpath}")
        grand += len(recs)
    print(f"\nTotal: {grand} records across {len(sources)} source(s).")
    print("Next: feed out/*.json into merge_dcn_corpus.py, then clean_titles.py + audit_data_quality.py.")


if __name__ == "__main__":
    main()
