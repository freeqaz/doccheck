# navstamp — breadcrumb stamper

**Hub:** [home](../README.md) — scope semantics, gloss sources, and what it refuses to touch

`navstamp` inserts the one-line navigation header that doccheck's `nav` and
`hubdist` checks measure:

```markdown
**Hub:** [<hub>](<rel>) › [<tier-2 index>](<rel>) — <5-12 words on what the doc establishes>
```

`**Hub:**` is only the default: the token and the window scanned for it follow
the `nav_token` / `nav_scan_lines` config keys, so the stamper and the `nav`
check can never disagree about what a stamped file looks like.

```sh
navstamp --root /path/to/docs --scope docs/guides            # dry run (default)
navstamp --root /path/to/docs --scope docs/guides --apply    # write
navstamp --root /path/to/docs --scope docs/guides/one.md --gloss "..." --apply
```

Sample dry run, over a small demo tree:

```console
$ navstamp --root demo-docs --no-config --scope docs/guides
  docs/guides/setup.md
      **Hub:** [docs](../README.md) — how to set the project up locally

scope=docs/guides  would stamp 1  skip 0  (dry run — pass --apply to write)
```

## Safety properties

The tool edits files in a tree that several concurrent doc streams may share,
so three properties are load-bearing (and pinned by the test suite):

- **`--scope` is mandatory and is a path prefix, not a string prefix.** There
  is no whole-tree mode: an unscoped pass would rewrite every file every other
  stream is mid-edit on, and the recovery is a manual diff review of thousands
  of files. `--scope sub` selects `sub/…` and the file `sub` itself — never
  the sibling `subway.md`. A scope that matches no docs exits `2`.
- **Idempotent.** Any file already carrying the token in its scan window is
  skipped untouched, so re-running a scope is safe.
- **Additive.** It inserts its own line plus one blank separator at line 1 and
  rewrites no other byte. Frozen provenance (`frozen_marks`, `size_exempt`) is
  skipped entirely — it is not ours to edit.

Dry run is the default; `--apply` is required to write. Review the diff on one
directory before running it over a whole prefix.

## Where the header comes from

The **hub** is the deepest hub (from `hubs`) whose directory contains the doc,
falling back to the nearest by link distance — containment beats distance
because the breadcrumb answers "where does this belong", not "what is the
shortest path anyone ever wrote". Its display name comes from `hub_names`,
else its directory name. A **tier-2 index** — the nearest ancestor directory
index that is not itself a hub — is added as a second crumb when one exists.

The **gloss** (the 5–12-word tail) is chosen by `--gloss-source`:

| source | behaviour |
| --- | --- |
| `auto` (default) | the index row that routes to this doc, else first prose, else the H1 |
| `index` | only prose an existing index already wrote next to its link here — the best gloss is usually one somebody already hand-wrote, and reusing it keeps header and index consistent |
| `prose` | first real sentence of the body (skipping banners, tables, blockquotes), else the H1 |
| `h1` | the doc's own H1 — for transcriptions and docs whose opening lines are front matter rather than a thesis |

`--gloss TEXT` overrides all of them (meant for single-file passes). Glosses
are clipped at a clause boundary rather than a hard word cut, so you get
"how the scorer ranks candidates" rather than "… so the".

## Skips

Every non-stamped file gets a reason (shown with `--show-skips`):

| reason | meaning |
| --- | --- |
| `already stamped` | token found in the scan window — idempotency |
| `frozen provenance` / `size-exempt provenance` | matches `frozen_marks` / `size_exempt` |
| `top of the hub tree — write its header by hand` | the doc is itself a hub; a hub's crumb points up, never at itself |
| `no gloss derivable — write one by hand` | no index row, prose or H1 yielded a usable phrase |
| `unreadable: …` | the file could not be opened |

Exit codes: `0` on a completed run (including a dry run), `2` for a config
error, an empty `--scope`, or a scope that matched no docs.
