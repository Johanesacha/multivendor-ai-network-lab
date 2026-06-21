# Candidate sources → commands.json extraction kit

Adds 8 free, official command-reference docs to **multivendor-cli-configurator**.
You (or other agents) run this locally to fetch the docs and emit JSON in your
exact corpus schema: `vendor, os, role, cat, title, cmd, desc`.

Why a kit and not finished JSON: the source docs are large vendor PDFs that must
be downloaded and parsed; command *syntax* is extracted (factual), book prose is
not. Nothing here is hand-fabricated command data — `sample_commands.json` is a
tiny verified seed that only shows the target format.

## Files

## Layout (as it sits in this repo)

The runnable kit is in `scripts/`; the reference data is in `docs/`. The scripts
auto-resolve the manifest in either location, so this split works as-is.

| File | Location | What it is |
|---|---|---|
| `fetch_sources.sh` | `scripts/` | Downloads each doc into `scripts/sources/<id>.<ext>` (run locally). |
| `parse_vendor_docs.py` | `scripts/` | Per-source parser scaffold → `scripts/out/<id>.json` in corpus schema. |
| `.gitignore` | `scripts/` | Keeps `sources/`, `out/`, and `*.pdf` out of git. |
| `candidate_sources.json` | `docs/` | Machine-readable manifest of the 8 ingestion targets — URL, vendor, os, role, category hints, license + parse notes. |
| `sample_commands.json` | `docs/` | 10 hand-verified records showing the exact schema. |
| `books.json` | `docs/` | Full 32-entry catalog of the wider reading list (superset of the 8); shares `id` with the manifest. |
| `BOOKS.md` | `docs/` | Human-readable version of the catalog. |

## Run it

```bash
cd scripts/                             # the kit lives here

# 1. download the source docs (needs internet; poppler recommended for parsing)
chmod +x fetch_sources.sh
./fetch_sources.sh                      # all, or: ./fetch_sources.sh cisco_ios_mcl
brew install poppler                    # or: pip install pypdf

# 2. parse to intermediate JSON (preview first with --limit)
python3 parse_vendor_docs.py --source extreme_exos_cref --limit 50
python3 parse_vendor_docs.py --all      # writes scripts/out/<id>.json

# 3. feed scripts/out/*.json into your existing pipeline
#    merge_dcn_corpus.py  ->  clean_titles.py  ->  audit_data_quality.py  ->  commands.json
```

## The 8 sources (priority order)

**High — new surface / big gap-fill**

1. `cisco_ios_mcl` — Cisco IOS Master Command List. Biggest single win; full IOS surface vs. the current ~4,409 from the portable guide.
2. `nokia_sros_7750` — Nokia 7750 **SR OS** (classic CLI). A *new NOS* (`os=sros`); today you only have SR Linux.
3. `nokia_srlinux_cli_plugin` + `nokia_srlinux_config_basics` — deepen SR Linux (currently 1,053), the AI-lab fabric vendor.

**Medium — category depth / accuracy (dedup against the above)**

5. `cisco_ios_lanswitch_cref` — VLAN/STP/switchport.
6. `cisco_ios_security_cref` — Security/Firewall/AAA.
7. `cisco_ios_fundamentals_cref` — System/management.
8. `extreme_exos_cref` — validate/refresh existing Extreme (2,781); cleanest parse of the set.

## Notes for the parsing agent

- `parse_vendor_docs.py` is a **scaffold** — the per-source `style_*` extractors are
  starting regexes. Tune them against the real `pdftotext -layout` dump; doc layouts
  vary. The EXOS and Cisco command-ref styles are the most reliable; the Nokia tree
  flattener needs the most refinement.
- Always **dedup by `cmd`** against the existing `commands.json` — the medium-priority
  Cisco refs overlap the MCL by design.
- Register `os=sros` as distinct from `os=srlinux`; they are different Nokia CLIs.
- Keep downloaded docs under the repo's gitignored `scripts/sources/`. Extract command
  syntax only; do not commit redistributed book text.
