# doccheck

Structural linting and navigability metrics for **large** Markdown
documentation trees — the checks a human reviewer cannot do once a docs tree
passes a few hundred files.

Three single-file tools, standard library only:

| tool | question it answers | detail |
| --- | --- | --- |
| [`doccheck.py`](doccheck.py) | is this doc broken? | [docs/checks.md](docs/checks.md) — every check category and its exemption rules |
| [`docgraph.py`](docgraph.py) | can a reader who landed here find their way? | [docs/docgraph.md](docs/docgraph.md) — metric definitions and baseline workflow |
| [`navstamp.py`](navstamp.py) | how do I add the breadcrumbs doccheck measures? | [docs/navstamp.md](docs/navstamp.md) — scope semantics and gloss sources |

**Status: alpha.** Battle-used, not battle-polished. These tools ran daily
against documentation trees of ~2,400 and ~3,000 Markdown files (5,000+
combined) inside a private research monorepo, but they were recently extracted
from it, so expect rough edges: generic placeholder names where
project-specific vocabulary used to be, and defaults that reflect one
project's conventions more than they should.

## Why

Ordinary Markdown linters check *style* inside a file. Past a few hundred docs
the defects that actually cost you time are **between** files, and they are all
invisible to a reviewer reading one page at a time:

- a link that resolved when it was written and stopped resolving when a file moved,
- an index row still labelled `CURRENT` above a doc whose own header says `SUPERSEDED`,
- a `#section` anchor pointing at a heading somebody renamed,
- a machine-specific `/home/<user>/…` path pasted into a runnable code block,
- and the one no mainstream linter has: **"latest pointer" rot.**

### Latest-pointer rot

A link whose *text* says "newest"/"latest" but whose *target* is a dated file:

```markdown
- [newest session handoff](sessions/SESSION_2026-08-20_TOPIC.md)
```

That line is correct the day it is written and wrong the day the next dated file
lands — in **every copy of it**. In the tree this was built for, one such
pointer had been stamped into 93 docs, so each new handoff required a 93-file
sweep commit just to keep the docs honest. The check reports it and names the
fix: point at the living index that pins the newest instead. The one lawful
carrier is the dated file's own directory index (`README.md`/`INDEX.md`) —
that index *is* the living surface everyone else should point at, so it is
exempt.

## Install

Zero runtime dependencies — each tool is one file, so the smallest install is
no install at all:

```sh
python3 doccheck.py --root /path/to/docs
```

(`docgraph.py` and `navstamp.py` import `doccheck.py`, so keep the three files
together.)

Or install the console scripts:

```sh
pip install .
doccheck --root /path/to/docs
```

Requires Python >= 3.9. `git` on `PATH` is optional: with it, links to
deliberately gitignored artifacts are not reported as broken; without it, the
checks degrade cleanly to "nothing is ignored".

## Quick start

```sh
doccheck --root /path/to/docs                  # all checks, human output
doccheck --root /path/to/docs --only links,anchors
doccheck --root /path/to/docs --json           # machine-readable
```

`--root` defaults to the current directory. The exit code is non-zero **only**
for categories named in `--fail-on`, so the judgement-dependent checks can run
without blocking CI:

```sh
doccheck --root . --fail-on links,rootpath,anchors,assets,index,latestptr
```

Exit codes: `0` clean (or findings only in ungated categories), `1` findings
in a `--fail-on` category, `2` usage or config error. Both `--only` and
`--fail-on` are validated against the known category list **before** the tree
walk — a typo'd category name is an error, not a gate that silently passes:

```console
$ doccheck --root . --fail-on lnks
doccheck: --fail-on names no known check category: lnks
  known: links, rootpath, anchors, assets, index, phrases, abspath, latestptr, stale, unreachable, coverage, size, orphans, nav, gloss, hubdist
  (--fail-on gates the exit code on categories, e.g. 'links,anchors')
$ echo $?
2
```

Other flags: `--max-lines N` (default 600, the `size` threshold), `--limit N`
(findings shown per category, default 25), `--dump-config` (print the
effective settings as JSON and exit).

## What it checks

Deterministic — a finding is always a defect, reasonable to gate in CI:

| check | finds |
| --- | --- |
| `links` | relative `.md` links that do not resolve from the linking doc |
| `rootpath` | links written as repo-root paths (they resolve from `/`, not from the doc) |
| `anchors` | `#section` links whose heading does not exist in the target |
| `assets` | non-`.md` link targets (tools, configs, images) that no longer exist |
| `index` | an index row calling a doc CURRENT/LIVE while the doc's own header says SUPERSEDED/RETRACTED |
| `abspath` | absolute machine paths (`/home/<user>/`, `/Users/<user>/`) in doc bodies |
| `latestptr` | "newest"/"latest" link text pointing at a dated file (see above) |
| `phrases` | wording your project has retired — **configurable, empty by default** |

Reported but never gated — a finding here can be legitimate mid-workstream:

| check | finds |
| --- | --- |
| `unreachable` | docs reachable from no declared root |
| `orphans` | docs nothing links to at all |
| `coverage` | docs their own directory's index does not list |
| `size` | docs over `--max-lines` |
| `stale` | state markers that outlived their ruling — **configurable, empty by default** |
| `nav` | active doc with no breadcrumb header (`**Hub:**` by default, configurable) |
| `gloss` | index row whose link carries no reason to follow it |
| `hubdist` | active doc more than N hops from any hub |

The interesting behaviour is in what these *don't* report. Inline code spans and
fenced blocks are quotation, not markup, so a link-shaped code fragment is not a
broken link. Links into sibling repos are pointers, not checkable links — the
alternative is a gate that is green on a dev box and red in CI over the same
tree. Deliberately gitignored targets are regenerable artifacts, not holes.
Frozen provenance (`archive/`, dated readouts) is exempt from the checks that
would ask you to edit it. Allow-lists match over a **window**, not a line,
because hard-wrapped prose routinely puts the exculpating clause on the next
line — and a lint that pushes an author to mangle correct prose is worse than
one that misses a case. The full rules are in
[docs/checks.md](docs/checks.md), with each check's exemptions spelled out.

## Configuration

Everything project-specific — which files are entry points, which are hubs,
which path markers mean "frozen provenance", and the retired-wording and
staleness pattern lists — lives in a JSON config, **not** in the code.
`.doccheck.json` beside the docs root is picked up automatically; `--config
PATH` names one explicitly, `--no-config` ignores both, and `--dump-config`
prints the effective settings. All three tools share the config and the
`--root`/`--config`/`--no-config` flags, so the linter, the metrics and the
stamper can never disagree about what a hub or a breadcrumb token is.

```json
{
  "roots": ["README.md", "docs/README.md"],
  "hubs": ["README.md", "docs/README.md", "docs/architecture/README.md"],
  "hub_names": {"docs/architecture/README.md": "architecture"},
  "frozen_marks": ["/archive/", "/history/"],
  "size_exempt": ["/transcripts/"],
  "phrases": [
    {"pattern": "only a proxy", "label": "coverage-is-only-a-proxy", "requires": "coverage"}
  ],
  "phrases_allow": "misreading|retired|superseded"
}
```

`phrases` is how you ban wording your team has retired. `requires` is a second
regex that must appear nearby for the rule to fire — most retired phrases are
only wrong in one sense, and the same words are ordinary vocabulary elsewhere.
`phrases_allow` exempts prose that quotes the retired phrasing *in order to*
retire it. Both lists ship **empty**, so neither check does anything until you
configure it. An unknown config key is an error rather than a silent no-op: a
typo'd key disables the rule its author meant to write, and that reads as a
clean tree.

[docs/configuration.md](docs/configuration.md) lists every key, its default
and its blast radius, plus the Python API.

## The three tools

- **`doccheck`** — the linter above. Also the shared library: link parsing,
  file discovery and configuration live here, and the other two import them so
  the three cannot disagree about what a link is.
- **`docgraph`** — navigability *metrics*, not defects. `doccheck` answers
  "is this doc broken?"; this answers "can a reader who landed here find their
  way?" — orphans, sinks (no outbound link), hop distance from the nearest hub,
  and `scancost`: how many links a reader must scan on the shortest path from a
  root. Use it as a before/after instrument when restructuring.
- **`navstamp`** — inserts the one-line `**Hub:** …` breadcrumb header that
  `nav` and `hubdist` measure. Purely additive (one line plus a blank separator
  at line 1, no other byte rewritten), idempotent, and `--scope` is
  **mandatory** — there is no whole-tree mode, because an unscoped pass rewrites
  every file every other concurrent editor is mid-edit on. Dry run by default;
  `--apply` writes. It prefers a gloss somebody already hand-wrote in the index
  row that routes to the doc, rather than inventing a second, divergent one.

## Tests

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest -q
```

No network, no fixtures on disk — every test builds a synthetic docs tree in a
temp dir. Most of them are regression pins for false positives found in
production, which is the failure mode that matters here: a docs linter nobody
trusts gets switched off.

## License

MIT — see `LICENSE`.
