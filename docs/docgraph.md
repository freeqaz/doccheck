# docgraph — navigability metrics

**Hub:** [home](../README.md) — what each metric means and how to baseline a restructuring

`doccheck` answers "is this doc broken?". `docgraph` answers "can a reader who
landed here find their way?" — the property hub-and-index work is trying to
move. It reports only; nothing here gates. Link parsing, file discovery,
`roots` and `hubs` are imported from `doccheck`, so the measurement and the
reported checks apply one rule.

```sh
docgraph --root /path/to/docs                # human summary
docgraph --root /path/to/docs --json         # machine-readable baseline
docgraph --root /path/to/docs --scope docs/guides   # one slice
```

Sample output, over a small four-doc tree:

```console
$ docgraph --root demo-docs --no-config
docgraph  scope=(whole tree)  active=3 of 4 tracked

  sinks (no outbound .md link)         0  (0.0%)
  orphans (no inbound link)            0  (0.0%)
  >2 hops from a hub, or no hub        0  (0.0%)
    of which unreachable from any      0
  scan cost  median 3  p90 3  max 4
  index rows glossed               100.0%  (4/4)

  largest index fan-out:
       3  docs/README.md
       1  README.md
```

## Metrics

| metric | meaning |
| --- | --- |
| `docs_active` | discovered docs minus frozen provenance (`frozen_marks`), the population every percentage is over |
| `sinks` | docs with no outbound resolving `.md` link — the back-edge deficit; a reader who lands here is stuck |
| `orphans` | docs no other doc links to (declared roots excepted) |
| `hub_gt2_or_unreachable` | docs more than `hubdist_max` hops from every hub, or reachable from none |
| `rootdist` / `root_unreachable` | same, from the declared `roots` — "is it reachable at all" vs the hubs' "did a cold landing find context" |
| `scancost` | links a reader must scan on the shortest root→doc path (sum of out-degree along the path, inclusive) — the "how much reading to get here" number; reported as median / p90 / max |
| `index_gloss_pct` | index rows whose link carries a ≥`gloss_min_words`-word reason to follow it |
| `top_fanout` | largest index files by row count — the pages that most need splitting |

## Baseline workflow

`--json` output is stable-keyed and sorted, so the intended use is a pinned
before/after comparison around a restructuring:

```sh
docgraph --root docs --json > baseline.json
# ... restructure ...
docgraph --root docs --json | diff baseline.json -
```

`--scope PREFIX` restricts the **active** population (and the per-index gloss
counts) to one path prefix, so a stream can measure its own slice while the
rest of the tree is mid-flight. The graph itself is always built over the
whole tree — hop distances through out-of-scope docs still count.

`--tier2` also counts rows in `INDEX_<SUBJECT>.md` files toward the gloss
metric. Off by default: `index_names` only covers `README.md`/`INDEX.md`, so
splitting a 143-row README into six subject indexes otherwise reads as 143
index rows deleted. Leave it off while a pinned baseline is being compared;
turn it on for the re-baseline afterwards.
