# CLI Corpus Expansion — Agent Handoff & Task List

**Goal:** add 8 free, official command-reference docs to the
`multivendor-cli-configurator` corpus (`commands.json`) by extracting *command
syntax* into the schema `{vendor, os, role, cat, title, cmd, desc}`.

**Where things live**

| What | Path |
|---|---|
| Runnable kit | `multivendor-ai-network-lab/scripts/` |
| Downloader | `scripts/fetch_sources.sh` |
| Parser (scaffold + per-source extractors) | `scripts/parse_vendor_docs.py` |
| Manifest of the 8 targets | `docs/candidate_sources.json` |
| Schema example | `docs/sample_commands.json` |
| Full reading catalog (32) | `docs/books.json` · `docs/BOOKS.md` |
| How-to-run | `docs/CLI_CORPUS_README.md` |
| Downloaded docs + parser output (gitignored) | `scripts/sources/` · `scripts/out/` |

**Hard rules**

- Extract command **syntax only** (factual). Never commit redistributed book
  prose or the source PDFs. `scripts/.gitignore` already excludes `sources/`,
  `out/`, and `*.pdf` — keep it that way.
- Do **not** fabricate command data. Every record must trace to a real line in a
  real source doc.
- Register `os=sros` (Nokia SR OS) as distinct from `os=srlinux` — different CLIs.

---

## Status

| Source id | Extractor | State | Notes |
|---|---|---|---|
| `cisco_ios_fundamentals_cref` | `style_cisco_cref` | ✅ **VALIDATED** on real text — 23 records, 0 dirty desc, all System | dual-mode regex: works on web (bold) **and** pdftotext (plain) |
| `cisco_ios_lanswitch_cref` | `style_cisco_cref` | 🟡 reuses validated extractor — run on real PDF | set default cat=VLAN/STP at source |
| `cisco_ios_security_cref` | `style_cisco_cref` | 🟡 reuses validated extractor — run on real PDF | cat=Security/Firewall |
| `cisco_ios_mcl` | `style_cisco_mcl` | ✅ **VALIDATED & MERGED** — 17,962 records, cat:Misc 0.1%, 17,706 net-new after dedup | book-codes peeled off the right (single-space, not column-aligned); full 73-code book→cat legend map |
| `nokia_sros_7750` | `style_nokia_sros` | ✅ **VALIDATED & MERGED** — 42 clean records, `os=sros` | dash-help + console-table rows; new NOS surface now live in commands.json |
| `extreme_exos_cref` | `style_exos_cref` | ⬜ **TODO** — untuned scaffold | cleanest remaining format (Syntax/Description blocks) |
| `nokia_srlinux_cli_plugin` | `style_srlinux_decl` | ⬜ **TODO** — untuned scaffold | declarative set/info/show paths |
| `nokia_srlinux_config_basics` | `style_srlinux_decl` | ⬜ **TODO** — untuned scaffold | merge + dedup with the plug-in guide |

> **Legend:** ✅ validated against real source text · 🟡 reuses an already-validated
> extractor, just needs a real-PDF run · 🟠 dedicated code exists but unproven on the
> real doc — validate before trusting · ⬜ scaffold only.
>
> **Category hint:** `parse_source` accepts an optional 4th tuple element from any
> extractor — `(title, cmd, desc, cat_hint)`. It's used only when keyword
> `categorize()` returns `Misc`. `style_cisco_mcl` already uses this (book-code → cat).
> New extractors may return the 3-tuple or the 4-tuple form.

---

## The proven tuning loop (repeat per source)

This is exactly how `cisco_ios_fundamentals_cref` was validated. Do the same for
each remaining source:

1. **Get the source text.** Run `./fetch_sources.sh <id>` to download the PDF into
   `scripts/sources/<id>.pdf`. (For a quick text-only check you can also save a
   `.txt` dump there — the parser reads any `<id>.*`.)
2. **Extract text.** Install poppler (`brew install poppler`) so the parser uses
   `pdftotext -layout`, or `pip install pypdf`.
3. **Run + eyeball.** `python3 parse_vendor_docs.py --source <id> --limit 50`.
   Inspect `scripts/out/<id>.json`.
4. **Check the three failure modes** (these are what bit the Cisco pass):
   - desc bleeding across entries / lines (drop DOTALL, anchor per line);
   - junk records from "no form of this command" negation sentences (guard them);
   - everything landing in `cat:Misc` (add keyword rules in `CAT_RULES`).
5. **Tune the `style_*` regex** until: 0 dirty descriptions, sensible categories,
   `cmd` is the real command name/syntax.
6. **Dedup by `cmd`** against the existing `commands.json` before merging — the
   medium-priority Cisco refs overlap the Master Command List by design.

### Quick validation snippet (used for Cisco — reuse it)

```python
import importlib.util, json
spec = importlib.util.spec_from_file_location("p", "parse_vendor_docs.py")
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
txt = open("sources/<id>.txt").read()
recs = p.style_<style>(txt)
print(len(recs), "records")
print([r for r in recs if "use the" in r[2].lower()])   # should be empty (no dirty desc)
```

---

## Per-source extractor notes

**Extreme EXOS (`style_exos_cref`)** — do this next; it's the cleanest untuned one.
The EXOS Command Reference prints each command as `Syntax\n<cmd>` then
`Description\n<text>` then `Example`. Tune the existing block regex against the real
`pdftotext -layout` dump (watch for multi-line wrapped syntax). Default
`role=switch`; map section headers (vlan/stp/ospf/acl) to `cat`.

**Cisco MCL (`style_cisco_mcl`)** — extractor already written: the MCL is an *index*
where each line is `command name + trailing uppercase book-codes` (e.g.
`aaa authentication login SEC`). It peels the book-codes off the right and maps the
first to a category. **Validate it** against the real `pdftotext` of `all-book.pdf`:
the MCL is huge and has page headers/range rows ("A through B") — confirm those are
filtered and that multi-word commands survive. This is the biggest single volume add.

**Nokia SR OS (`style_nokia_sros`)** — extractor already written: it reads the SR OS
"CLI Usage" guide in two reliable shapes — dash-help (`back   - Go back a level`) and
console-table (`ping   Verifies ...   100`). **Validate it** against the real doc and
confirm prose lines aren't mistaken for commands. `os=sros` (distinct from srlinux).

**Nokia SR Linux (`style_srlinux_decl`)** — still a scaffold. Declarative CLI: capture
`set /...`, `info from state ...`, and `show ...` paths. `os=srlinux`, `role=switch`.
Parse both the CLI Plug-in Guide and Config Basics, then dedup by `cmd`.

---

## Done = merge into the live corpus

After all `scripts/out/*.json` look clean:

```
scripts/out/*.json
   -> merge_dcn_corpus.py        # merge + dedupe across sources
   -> clean_titles.py            # repair any prose-in-title records (idempotent)
   -> audit_data_quality.py      # flag leaked prose; quarantine unrecoverable
   -> commands.json              # single source of truth
   -> commit + push to main      # GitHub Pages auto-deploys the live demo
```

**Acceptance check per source:** records > 0, 0 dirty descriptions (`use the`/`**`
not present in `desc`), < ~5% `cat:Misc`, and `cmd` values are real commands that
`docker exec`/device CLIs would accept. Spot-check 10 records against the source doc.

---

*Reference implementation to copy: `style_cisco_cref` in `parse_vendor_docs.py`
(dual-mode bold/plain regex, per-line anchoring, no-form guard, mode capture).*
