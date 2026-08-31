# doccheck check reference

**Hub:** [home](../README.md) — every check category and its exemption rules

Sixteen categories, in two tiers. The tier is a design decision, not an
implementation detail: a **deterministic** finding is always a defect, so
gating CI on it never blocks legitimate work; a **reported** finding can be
legitimate mid-workstream (a doc being drafted is allowed to be an orphan), so
`doccheck` never fails on one unless you name it in `--fail-on` yourself.

Run a subset with `--only links,anchors`; gate the exit code with
`--fail-on links,anchors`. Both flags name categories from the list below and
both are validated **before** the tree walk — an unknown name exits `2` with
the known list, because a typo'd `--fail-on` would otherwise gate nothing and
exit `0` forever.

Exit codes: `0` clean or ungated, `1` findings in a gated category, `2` usage
or config error.

## Deterministic (safe to gate)

### `links`

A relative `.md` link that does not resolve from the linking doc. A trailing
slash means "this directory's index": `[sessions](./sessions/)` resolves if
`sessions/README.md` or `sessions/INDEX.md` exists, and is a broken directory
link only when the directory itself is missing.

### `rootpath`

A link written as a repo-root path that happens to resolve from the root but
not from the doc that carries it. GitHub renders these; most other viewers and
`doccheck`'s own graph do not, and they break the moment the doc moves. The
link still counts as an edge for reachability — the finding asks you to
rewrite it, not to treat the target as unreachable.

### `anchors`

A `#section` fragment naming a heading that does not exist in the target doc.
Slugs are computed GitHub-style, and both spellings of punctuation collapse
(`lives--and` and `lives-and`) are accepted rather than reporting a link the
renderer resolves fine.

### `assets`

A non-`.md` link target (script, config, image) that does not exist, checked
both relative to the doc and relative to the root. Targets containing `*` are
skipped (glob patterns are prose, not links).

### `index`

An index row whose status column says `CURRENT`/`LIVE`/`GOVERNING`/`PLAN OF
RECORD`/`OF RECORD` while the target doc's own first 30 lines say
`SUPERSEDED`/`STALE-BANNER`/`RETRACTED`/`DO NOT CITE`. Only rows in files
named per `index_names` (default `README.md`/`INDEX.md`) are checked.

### `abspath`

An absolute machine path (`/home/<user>/`, `/Users/<user>/`) in a doc body —
the copy-paste nobody else can run. Scanned over the whole line *including*
code fences, precisely because a machine path inside a runnable ```` ```bash ````
block is the worst place for one. Placeholders like `/home/<user>/` and
`/Users/$USER/` do not match. Exempt: frozen provenance, `size_exempt` paths,
`abspath_exempt` paths (handoffs, sessions, readouts by default), and any file
with a date in its name — the path a run happened on is a fact about that run.

### `latestptr`

A link whose text says "newest"/"latest" but whose target is a dated file
(`20xx-xx-xx` in the name). Such a pointer rots in every copy the day the next
dated file lands. The one lawful carrier is the dated file's own directory
index — that *is* the living surface everyone else must point at, so an index
naming the newest file in its own directory is exempt.

### `phrases`

Wording your project has retired, from the `phrases` config key — **empty by
default**, so the check is inert until configured. Each rule is a pattern, a
label, and an optional `requires` regex that must also appear in the context
window, which keeps a rule off the correct sense of the same words.
`phrases_allow` exempts prose that quotes the retired phrasing in order to
retire it. Matched against the raw line even inside code fences: a retired
framing quoted in a block is still on the page.

## Reported, never gated

### `unreachable`

A doc with incoming links that is still not reachable from any declared root
(`roots`). Reachability is the property indexes exist to provide; raw
in-degree is satisfiable by two orphans pointing at each other.

### `orphans`

A doc with no incoming links at all.

### `coverage`

A doc its own directory's index (`README.md`/`INDEX.md` in the same
directory) does not list. Only directories that *have* an index are checked.

### `size`

A doc longer than `--max-lines` (default 600). Exempt: dated filenames and
`size_exempt` paths — verbatim transcriptions and frozen readouts are
provenance, and splitting one churns history for no findability gain.

### `stale`

State markers that outlived the decision behind them, from the `stale` config
key — **empty by default**. `stale_allow` (default: prose containing
"superseded", "predates", "no longer", …) exempts lines that already
acknowledge the marker is historical. Dated append-only log lines
(`- 2026-01-02 …`) are never reported: editing one would falsify the log.

### `nav`

An active doc without the breadcrumb token (`nav_token`, default `**Hub:**`)
in its first `nav_scan_lines` (default 5) lines. Hubs themselves are exempt —
a hub is the crumb's destination. [`navstamp`](navstamp.md) writes exactly the
header this check looks for, reading the same config keys.

### `gloss`

An index row whose `.md` link carries fewer than `gloss_min_words` (default 5)
words of trailing prose saying why to spend a hop on it.

### `hubdist`

An active doc more than `hubdist_max` (default 2) link-hops from every hub in
`hubs`, or reachable from none. More than two hops means the reader took three
guesses to arrive.

## What is deliberately not reported

These rules exist because each removed a class of false positives found in
production; the regression tests pin them.

- **Excluded trees.** Directories in `exclude_dirs` (`.git`, `node_modules`,
  caches …) and paths matching `exclude_path_parts` (`/.venv`, `/out/`,
  `/runs/`, `/work/` …) are never discovered at all. Generated scratch trees
  hold machine-written READMEs whose links only ever resolved on the box that
  made them.
- **Archive.** Nothing inside `archive_mark` (default `/archive/`) is ever
  reported on — it is unedited provenance. Its files still count as link
  *targets*, so linking into the archive is fine.
- **Frozen provenance.** Paths matching `frozen_marks` are exempt from the
  navigability checks (`nav`/`gloss`/`hubdist`): navigability is not a
  property we ask of frozen material, and editing it is usually forbidden.
- **Code fences and inline code spans.** Link syntax inside them is quotation,
  not markup. A python block containing `FN[m.config.model_type](model=m)`
  parses as a link to `model=m`; reporting it is a finding about the checker.
  Closing fences must match the opener's character, so a ``` inside a ~~~
  block does not end it early.
- **Gitignored targets.** A link to a path `.gitignore` deliberately excludes
  points at a regenerable artifact, not a hole in the docs — it is present in
  a working checkout and absent in CI, so reporting it makes the same tree
  green locally and red in CI. Resolved with one batched `git check-ignore`
  call; degrades to "nothing is ignored" when git is unavailable.
- **Out-of-tree links.** `../sibling-repo/...` is a pointer, not a checkable
  link: whether the sibling is checked out beside you is a property of the
  machine, not of the documentation.
- **Context windows.** Phrase/staleness allow-lists match over a window of
  `allow_radius` lines (default 2), not a single line, because hard-wrapped
  prose routinely puts the exculpating clause on the next line. The cost is
  accepted: a genuine violation near an unrelated allow-word is missed, which
  is the better error — a false positive pushes an author to mangle correct
  prose.
