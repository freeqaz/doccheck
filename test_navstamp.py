"""Pins for navstamp: it must be scoped, idempotent, and purely additive.

The tool edits files that several concurrent doc streams may share one working
tree for, so these three properties are what make it safe to run at all.
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doccheck  # noqa: E402
import navstamp  # noqa: E402

TOOL = Path(navstamp.__file__)


def _tree(tmp_path):
    (tmp_path / "README.md").write_text(
        "# root\n\n| [note](sub/note.md) | establishes the thing this note is about |\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "note.md").write_text("# note\n\nSome body prose that stays.\n")
    return tmp_path


def test_scope_is_mandatory():
    """There is deliberately no whole-tree mode; an unscoped pass is the hazard."""
    p = subprocess.run([sys.executable, str(TOOL)], capture_output=True, text=True)
    assert p.returncode != 0
    assert "--scope" in p.stderr


def test_stamp_is_purely_additive(tmp_path):
    root = _tree(tmp_path)
    before = (root / "sub" / "note.md").read_text()
    rows = navstamp.plan(str(root), "sub")
    assert navstamp.apply_rows(str(root), rows) == 1
    after = (root / "sub" / "note.md").read_text()
    head, _, body = after.partition("\n\n")
    assert head.startswith(navstamp.STAMP_TOKEN)
    assert body == before


def test_gloss_is_taken_from_the_index_row_that_already_routes_there(tmp_path):
    """Reuse the hand-written reason rather than inventing a second, divergent one."""
    root = _tree(tmp_path)
    rows = [r for r in navstamp.plan(str(root), "sub") if r["action"] == "stamp"]
    assert "establishes the thing this note is about" in rows[0]["header"]


def test_second_pass_changes_nothing(tmp_path):
    root = _tree(tmp_path)
    navstamp.apply_rows(str(root), navstamp.plan(str(root), "sub"))
    snapshot = (root / "sub" / "note.md").read_text()
    rows = navstamp.plan(str(root), "sub")
    assert all(r["action"] == "skip" for r in rows)
    assert navstamp.apply_rows(str(root), rows) == 0
    assert (root / "sub" / "note.md").read_text() == snapshot


def test_frozen_paths_are_never_touched(tmp_path):
    root = _tree(tmp_path)
    for rel in ("sub/archive/old.md", "sub/history/h.md"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# frozen\n")
    rows = {r["file"]: r for r in navstamp.plan(str(root), "sub")}
    for rel in ("sub/archive/old.md", "sub/history/h.md"):
        assert rows[rel]["action"] == "skip", rows[rel]


def test_configured_size_exempt_paths_are_never_touched(tmp_path):
    """`size_exempt` ships empty; a project that declares one gets it honoured
    by the stamper too, not just by the size check."""
    root = _tree(tmp_path)
    (root / "sub" / "x_TRANSCRIPT.md").write_text("# frozen by convention\n")
    rows = {r["file"]: r
            for r in navstamp.plan(str(root), "sub", config={"size_exempt": ["_TRANSCRIPT"]})}
    assert rows["sub/x_TRANSCRIPT.md"]["action"] == "skip", rows["sub/x_TRANSCRIPT.md"]
    assert doccheck.SIZE_EXEMPT == ()


def test_hub_label_comes_from_config_when_given(tmp_path):
    """A path is a poor breadcrumb label, so hubs may be named in the config."""
    root = _tree(tmp_path)
    rows = [r for r in navstamp.plan(str(root), "sub",
                                     config={"hub_names": {"README.md": "handbook"}})
            if r["action"] == "stamp"]
    assert rows[0]["header"].startswith("**Hub:** [handbook](")
    # Unnamed hubs fall back to a derived label, never the raw path.
    assert doccheck.hub_label("README.md") == "home"
    assert doccheck.hub_label("docs/guides/README.md") == "guides"


def test_out_of_scope_files_are_not_planned(tmp_path):
    root = _tree(tmp_path)
    planned = {r["file"] for r in navstamp.plan(str(root), "sub")}
    assert "README.md" not in planned
