# Configuration reference

**Hub:** [home](../README.md) — every config key, its default, and the Python API

All three tools read the same JSON config, in the same order:

1. `--config PATH` — an explicitly named file. Naming one that does not exist
   is an error, not a silent fallback: a linter that quietly checks nothing is
   worse than one that refuses to start.
2. `<root>/.doccheck.json` — picked up automatically if present.
3. Built-in defaults (listed below).

`--no-config` skips step 2. `doccheck --dump-config` prints the effective
settings as JSON — its output is itself a valid config file, so it doubles as
a template. An **unknown key is an error** (exit `2`, with the known-key
list): a typo'd key would silently disable the rule its author meant to
write, and that reads as a clean tree.

## Keys

### Graph shape

| key | default | meaning |
| --- | --- | --- |
| `roots` | `["README.md", "docs/README.md"]` | entry points the tree must be reachable from; feeds `unreachable`/`orphans` and docgraph's `rootdist`/`scancost` |
| `hubs` | `["README.md", "docs/README.md"]` | the small closed set of pages a reader holds in their head; feeds `hubdist`, docgraph's hub distance, and navstamp's breadcrumb target |
| `hub_names` | `{}` | display name per hub path, used in the breadcrumb navstamp writes; anything unnamed falls back to its directory name |
| `hubdist_max` | `2` | hops from the nearest hub beyond which `hubdist` reports |
| `index_names` | `["README.md", "INDEX.md"]` | filenames that count as a directory's index, for the `index`, `coverage` and `gloss` checks and directory links |

### Breadcrumb header

| key | default | meaning |
| --- | --- | --- |
| `nav_token` | `"**Hub:**"` | the token the `nav` check looks for and navstamp writes — one key drives both, so they cannot disagree |
| `nav_scan_lines` | `5` | how far down the token is accepted; tolerates a hand-stamped doc that put it just under its H1 |
| `gloss_min_words` | `5` | words of trailing prose an index-row link needs to count as glossed (`gloss` check and docgraph's gloss metric) |

### Scope and exemptions

| key | default | meaning |
| --- | --- | --- |
| `exclude_dirs` | `.git`, `node_modules`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, `worktrees`, `site-packages`, `.mypy_cache` | directory names never walked |
| `exclude_path_parts` | `/.venv`, `/venv/`, `/node_modules/`, `/site-packages/`, `/out/`, `/tmp/`, `/runs/`, `/work/` | path substrings never discovered (generated scratch trees) |
| `archive_mark` | `"/archive/"` | unedited provenance: never reported on, but still a valid link target |
| `frozen_marks` | `["/archive/", "/history/", "/testfixtures/"]` | frozen provenance: exempt from `nav`/`gloss`/`hubdist`, skipped by navstamp, excluded from docgraph's active set |
| `size_exempt` | `[]` | path markers exempt from the `size` check (transcriptions, generated corpora); also skipped by navstamp |
| `abspath_exempt` | `["/handoff/", "/handoffs/", "/sessions/", "/readouts/"]` | dated-record paths exempt from `abspath` **only** — deliberately not folded into `frozen_marks`, which would also silence the navigability checks on every handoff |

Files with a date in the name (`20xx-xx-xx`) are additionally exempt from
`size` and `abspath` with no configuration — a dated filename marks a frozen
readout.

### Pattern lists

| key | default | meaning |
| --- | --- | --- |
| `phrases` | `[]` | retired wording, as `{"pattern": …, "label": …, "requires": …}` objects; `label` defaults to the pattern, `requires` is an optional second regex that must appear in the context window |
| `phrases_allow` | matches nothing | regex exempting prose that quotes retired phrasing in order to retire it |
| `stale` | `[]` | outlived state markers, as `{"pattern": …, "label": …}` objects |
| `stale_allow` | `superseded\|predates\|subsequently\|scoped to\|no longer\|was ratified` | regex exempting lines that already acknowledge the marker is historical |
| `allow_radius` | `2` | lines either side of a match joined into the context window that `requires` and the allow regexes are tested against |

All patterns are matched case-insensitively.

## Example

```json
{
  "roots": ["README.md", "docs/README.md"],
  "hubs": ["README.md", "docs/README.md", "docs/architecture/README.md"],
  "hub_names": {"docs/architecture/README.md": "architecture"},
  "nav_token": "**Nav:**",
  "frozen_marks": ["/archive/", "/history/"],
  "size_exempt": ["/transcripts/"],
  "phrases": [
    {"pattern": "only a proxy", "label": "coverage-is-only-a-proxy", "requires": "coverage"}
  ],
  "phrases_allow": "misreading|retired|superseded"
}
```

## Python API

`doccheck.py` is also the library the other two tools build on. The useful
surface:

```python
import doccheck, docgraph, navstamp

# One-shot, with a config applied for the duration of the call:
findings = doccheck.check(root, max_lines=600, config={"hubs": ["README.md"]})
metrics  = docgraph.measure(root, scope="docs/guides", config=cfg)
rows     = navstamp.plan(root, scope="docs/guides", config=cfg)

# Or manage the config yourself:
cfg = doccheck.load_config(root)          # --config / .doccheck.json / {}
with doccheck.config_applied(cfg):        # context manager, restores on exit
    ...
prev = doccheck.apply_config(cfg)         # returns previous values
doccheck.restore_config(prev)
doccheck.effective_config()               # settings currently in force, config-file shape
```

Configuration is applied by setting module globals on `doccheck`, and the
values `docgraph` and `navstamp` share (`HUBS`, `ROOTS`, `FROZEN_MARKS`,
`GLOSS_MIN_WORDS`, navstamp's `STAMP_TOKEN`) are **forwarded at
attribute-access time** rather than copied at import — so a config change is
seen by all three modules at once and a stale copy cannot make the
measurement and the check drift apart.
