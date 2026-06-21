# Books & CLI References for the Command Corpus

Curated, **free / openly-available** vendor docs, books, and cheatsheets to enrich the
CLI/command RAG (`cli_corpus/` + `src/cli_rag.py`, BM25 over vendor CLI syntax).

**Scope:** free official vendor docs, open-licensed books, and automation/AIOps titles only —
no paywalled material. The `Ingest?` column flags what is worth feeding the BM25 corpus
(short, structured, command-dense) vs. what is background reading.

> **Ingestion note:** vendor command-reference PDFs are command-dense and ideal for BM25.
> Run them through `cli_corpus/ingest_md.py` after converting PDF→text/markdown. Conceptual
> books (background reading) help design but add noise to a command index — keep them out of
> the corpus or ingest only the command-table sections. **Respect each source's license** —
> the official PDFs below are free to download for reference; redistribution terms vary, so
> keep ingested copies local to the lab.

---

## 1. Cisco (IOS / IOS-XE / NX-OS)

| Resource | Type | License | Ingest? | Link |
|---|---|---|---|---|
| Cisco IOS Master Command List (all releases) | Command ref | Free (Cisco) | ✅ Yes | https://www.cisco.com/c/en/us/td/docs/ios-xml/ios/mcl/allreleasemcl/all-book.pdf |
| Cisco IOS Configuration Fundamentals Command Reference | Command ref | Free (Cisco) | ✅ Yes | https://www.cisco.com/c/en/us/td/docs/ios/fundamentals/command/reference/cf_book.pdf |
| Cisco IOS LAN Switching Command Reference | Command ref | Free (Cisco) | ✅ Yes | https://www.cisco.com/c/en/us/td/docs/ios/lanswitch/command/reference/lsw_book.pdf |
| Cisco IOS Security Command Reference | Command ref | Free (Cisco) | ✅ Yes | https://www.cisco.com/c/en/us/td/docs/ios/security/command/reference/sec_cr_book.pdf |
| Cisco IOS Configuration Fundamentals Configuration Guide | Config guide | Free (Cisco) | ◐ Partial | https://www.cisco.com/c/en/us/td/docs/ios/fundamentals/command/reference/cf_book.html |

## 2. Juniper (Junos)

| Resource | Type | License | Ingest? | Link |
|---|---|---|---|---|
| Junos OS CLI User Guide | CLI guide | Free (Juniper) | ✅ Yes | https://www.juniper.net/documentation/us/en/software/junos/cli/cli.pdf |
| Day One: Exploring the Junos CLI (2nd Ed) | Book (PDF) | Free (Juniper) | ✅ Yes | https://www.juniper.net/documentation/en_US/day-one-books/ExploreJunosCLI_2ndEd.pdf |
| Day One: Beginner's Guide to Learning Junos | Book (PDF) | Free (Juniper) | ◐ Partial | https://www.juniper.net/documentation/en_US/day-one-books/junos-beginners-guide.pdf |
| Juniper Day One Books library (full index) | Book series | Free (Juniper) | ◐ Browse | https://www.juniper.net/documentation/jnbooks/us/en/day-one-books |

## 3. Arista (EOS)

| Resource | Type | License | Ingest? | Link |
|---|---|---|---|---|
| Arista EOS User Manual (current) | Manual + CLI | Free (Arista) | ✅ Yes | https://www.arista.com/assets/data/pdf/user-manual/um-books/EOS-User-Manual.pdf |
| EOS Command-Line Interface (CLI) — online | CLI guide | Free (Arista) | ✅ Yes | https://www.arista.com/en/um-eos/eos-command-line-interface-cli |
| Arista cheatsheet (antonflor/cheatsheets) | Cheatsheet (md) | Open (GitHub) | ✅ Yes | https://github.com/antonflor/cheatsheets/blob/main/arista.md |

## 4. Nokia (SR Linux / SR OS)

| Resource | Type | License | Ingest? | Link |
|---|---|---|---|---|
| SR Linux CLI Plug-in Guide (24.10) | CLI guide | Free (Nokia) | ✅ Yes | https://documentation.nokia.com/srlinux/24-10/books/pdf/CLI_Plug-in_Guide_24.10.pdf |
| SR Linux Configuration Basics (21.3) | Config guide | Free (Nokia) | ✅ Yes | https://documentation.nokia.com/cgi-bin/dbaccessfilename.cgi/3HE16819AAAATQZZA01_V1_SR%20Linux%20R21.3%20Configuration%20Basics.pdf |
| SR Linux System Management (21.6) | Config guide | Free (Nokia) | ◐ Partial | https://documentation.nokia.com/cgi-bin/dbaccessfilename.cgi/3HE17568AAAA01_V1_SR%20Linux%20R21.6%20System%20Management%20Guide.pdf |
| SR Linux user-doc HTML index | Doc portal | Free (Nokia) | ◐ Browse | https://infocenter.nokia.com/public/SRLINUX200R6A/topic/org.nokia.help.all/html/index.html |
| 7750 SR OS Basic System Config — CLI Usage | CLI guide | Free (Nokia) | ✅ Yes | https://documentation.nokia.com/html/0_add-h-f/93-0070-10-01/7750_SR_OS_System_Basics_Guide/CLI%20Usage.pdf |

> Directly relevant to the lab — the CLOS EVPN-VXLAN fabric runs **Nokia SR Linux**.

## 5. Extreme Networks (ExtremeXOS / Switch Engine)

| Resource | Type | License | Ingest? | Link |
|---|---|---|---|---|
| ExtremeXOS Command Reference (31.5) | Command ref | Free (Extreme) | ✅ Yes | https://documentation.extremenetworks.com/exos_commands_31.5/downloads/EXOS_Command_Reference_31.5.pdf |
| ExtremeXOS Command Reference (30.5) | Command ref | Free (Extreme) | ✅ Yes | https://documentation.extremenetworks.com/exos_commands_30.5/downloads/EXOS_Command_Reference_30_5.pdf |
| ExtremeXOS Quick Guide | Cheatsheet | Free (Extreme) | ✅ Yes | https://documentation.extremenetworks.com/PDFs/EXOS/EXOS_Quick_Guide.pdf |
| ExtremeXOS User Guide (32.3) — online | User guide | Free (Extreme) | ◐ Browse | https://documentation.extremenetworks.com/exos_32.3/ |

## 6. FRRouting (FRR) — used by the lab backbone

| Resource | Type | License | Ingest? | Link |
|---|---|---|---|---|
| FRR User Guide (BGP) | Config guide | Open (GPL/CC) | ✅ Yes | https://docs.frrouting.org/en/latest/bgp.html |
| FRR User Guide (OSPFv2) | Config guide | Open (GPL/CC) | ✅ Yes | https://docs.frrouting.org/en/latest/ospfd.html |
| FRR User Guide (full, latest) | Doc set | Open | ◐ Browse | https://docs.frrouting.org/en/latest/ |

> Directly relevant — the 10-node `network-lab/` backbone is FRR.

---

## 7. Open / free networking books (background + design)

| Resource | Author | License | Ingest? | Link |
|---|---|---|---|---|
| Computer Networks: A Systems Approach | Peterson & Davie | **CC BY 4.0** | ◐ Concept only | https://book.systemsapproach.org/ |
| → source repo | — | CC BY 4.0 | — | https://github.com/SystemsApproach/book |
| Computer Networking (Kurose & Ross) companion site | Kurose & Ross | Free site | ◐ Concept only | https://gaia.cs.umass.edu/kurose_ross/index.php |

## 8. Network automation / AIOps (Python · Ansible · NetDevOps)

| Resource | Author | License | Ingest? | Link |
|---|---|---|---|---|
| Python for Network Engineers (HTML/PDF/ePub) | N. Samoylenko | **Free ebook** | ◐ Concept only | https://pyneng.readthedocs.io/en/latest/ |
| → exercises (with tests) | — | Open (GitHub) | — | https://github.com/natenka/pyneng-examples-exercises-en |
| → answers | — | Open (GitHub) | — | https://github.com/natenka/pyneng-answers-en |
| awesome-network-automation (curated index) | Network to Code | Open (GitHub) | ◐ Index | https://github.com/networktocode/awesome-network-automation |
| Practical Network Automation (code repo) | Packt | Open code | ◐ Code | https://github.com/PacktPublishing/Practical-Network-Automation-Second-Edition |

## 9. Multi-vendor tooling repos (parsers / CLI helpers)

| Resource | Purpose | License | Link |
|---|---|---|---|
| ciscoconfparse2 | Parse/audit/query Arista/Cisco/Juniper/PAN/F5 configs | Open (GitHub) | https://github.com/mpenning/ciscoconfparse2 |
| netcli-highlight | Syntax highlighting for Junos/IOS/NX-OS/EOS via SSH | Open (GitHub) | https://github.com/danielmacuare/netcli-highlight |
| antonflor/cheatsheets | Cisco/Juniper/Arista quick-reference command sheets | Open (GitHub) | https://github.com/antonflor/cheatsheets |

---

## Suggested next step for the corpus

The ✅ rows in sections 1–6 are the highest-value adds: they are vendor command references —
exactly the short, token-dense entries BM25 ranks well. A reasonable ingestion order, matched
to what the lab actually runs:

1. **Nokia SR Linux** (CLI Plug-in + Config Basics) — the EVPN fabric vendor.
2. **FRR** (BGP + OSPF) — the backbone.
3. **Arista EOS** (User Manual + cheatsheet) — the cEOS leaf/spine.
4. **Cisco** (Master Command List) and **Juniper** (CLI User Guide) — broadest coverage for the AI Coordinator's general queries.
5. **Extreme** last — not in the current lab, but rounds out vendor breadth.

Convert each PDF → text/markdown, then run `cli_corpus/ingest_md.py`. Keep sections 7–8
(conceptual books) **out** of the BM25 index — cite them in design docs instead.

---

*Compiled 2026-06-21. Links are official vendor / open-source sources; verify the version
matches your target NOS before ingesting, as command syntax drifts across releases.*
