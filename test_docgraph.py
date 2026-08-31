"""Pins docgraph's navigability metrics against a synthetic tree with known shape."""
from __future__ import annotations

import docgraph


def _tree(tmp_path, files: dict) -> str:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_sink_and_orphan_are_different_defects(tmp_path):
    """A doc linked-to but linking nowhere is a sink, not an orphan, and vice versa."""
    root = _tree(tmp_path, {
        "README.md": "# root\n\n- [leaf](leaf.md) the thing this establishes here\n",
        "leaf.md": "# leaf\n\nno links at all\n",
        "lonely.md": "# lonely\n\n[back](README.md) returns to the root index page\n",
    })
    m = docgraph.measure(root)
    assert m["sinks"] == 1          # leaf.md
    assert m["orphans"] == 1        # lonely.md (README.md is a ROOT, exempt)


def test_links_inside_a_code_fence_are_not_edges(tmp_path):
    """A markdown link quoted inside a fenced block is code, so it creates no edge."""
    root = _tree(tmp_path, {
        "README.md": "# root\n\n```\n[fake](leaf.md)\n```\n",
        "leaf.md": "# leaf\n",
    })
    m = docgraph.measure(root)
    assert m["sinks"] == 2          # neither doc has a real outbound edge
    assert m["orphans"] == 1        # leaf.md is not actually linked


def test_gloss_requires_a_reason_to_follow_the_link(tmp_path):
    """An index row counts as glossed only when prose after the link explains it."""
    root = _tree(tmp_path, {
        "README.md": (
            "# index\n\n"
            "| [a](a.md) | establishes the tuned baseline and its caveats | LIVE |\n"
            "| [b](b.md) | |\n"
        ),
        "a.md": "# a\n", "b.md": "# b\n",
    })
    m = docgraph.measure(root)
    assert m["index_rows_linked"] == 2
    assert m["index_rows_glossed"] == 1


def test_hub_distance_counts_hops_not_reachability(tmp_path):
    """A doc four hops down a chain is far from its hub even though it is reachable."""
    root = _tree(tmp_path, {
        "README.md": "[one](one.md) first hop away from the root index\n",
        "one.md": "[two](two.md) second hop along the chain\n",
        "two.md": "[three](three.md) third hop along the chain\n",
        "three.md": "# three\n",
    })
    m = docgraph.measure(root)
    assert m["hub_unreachable"] == 0
    assert m["hub_gt2_or_unreachable"] == 1     # three.md sits at hop 3


def test_tier2_index_files_are_off_the_gloss_population_by_default():
    """`INDEX_<SUBJECT>.md` rows are invisible to the gloss metric unless asked for.

    A pinned baseline is normally measured without them; counting them by default
    would silently re-scale every stream's before/after.
    """
    assert docgraph.is_index("docs/guides/x/README.md", False)
    assert docgraph.is_index("docs/guides/x/INDEX.md", False)
    assert not docgraph.is_index("docs/guides/x/INDEX_RANKER.md", False)
    assert docgraph.is_index("docs/guides/x/INDEX_RANKER.md", True)
    assert not docgraph.is_index("docs/guides/x/SOME_DOC.md", True)


def test_measure_honours_a_config_without_leaking_it(tmp_path):
    """Hubs are configurable, and the override must not outlive the call."""
    root = _tree(tmp_path, {
        "README.md": "# unlinked root\n",
        "docs/INDEX.md": "# real hub\n\n[a](a.md) the doc this hub routes readers to\n",
        "docs/a.md": "# a\n",
    })
    m = docgraph.measure(root, config={"hubs": ["docs/INDEX.md"],
                                       "roots": ["docs/INDEX.md"]})
    assert m["hub_unreachable"] == 1            # only README.md is off the hub graph
    assert docgraph.HUBS == ("README.md", "docs/README.md")
