"""The notes of a release: what is listed, under which heading, and how a line reads."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("release_notes", Path(__file__).parent.parent / "scripts" / "release_notes.py")
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)

REPO = "MOG-My-Own-Games/MOG-Client"
SHA = "d533520" + "0" * 33


def test_feat_goes_to_changes_and_fix_to_fixes_without_their_type():
    commits = [
        (SHA, "feat(install): let just copy be the first step of an install chain"),
        ("31ffb9e" + "0" * 33, "fix(auto_mode): do not take the word close in a sentence for a way forward"),
    ]
    out = notes.sections(commits, REPO)
    changes, fixes = out.split("## Fixes")
    assert "- install: let just copy be the first step of an install chain [[d533520](" in changes
    assert "- auto_mode: do not take the word close in a sentence for a way forward [[31ffb9e](" in fixes
    assert "feat" not in out and "fix(" not in out


def test_the_hash_links_to_its_commit():
    out = notes.sections([(SHA, "feat(a): b")], REPO)
    assert f"[[d533520](https://github.com/{REPO}/commit/{SHA})]" in out


def test_chore_doc_and_the_like_are_left_out():
    commits = [(SHA, f"{kind}(x): y") for kind in ("chore", "doc", "docs", "res", "test", "style", "refactor")]
    assert notes.sections(commits, REPO) == ""


def test_a_breaking_change_is_a_change_whatever_its_type():
    out = notes.sections([(SHA, "fix(api)!: drop the old route"), ("a" * 40, "chore: x\n\nBREAKING CHANGE: y")], REPO)
    assert out.startswith("## Changes") and "## Fixes" not in out
    assert out.count("\n- ") == 2  # the chore with the footer too
    assert "api: drop the old route" in out


def test_a_commit_without_a_scope_is_just_its_text():
    assert notes.sort([(SHA, "feat: show a thing")])[0] == [("show a thing", SHA)]


def test_a_subject_that_is_not_a_conventional_commit_is_a_fix():
    assert notes.sort([(SHA, "Add support for older EULA pages")])[1] == [("Add support for older EULA pages", SHA)]


def test_the_body_ends_with_the_link_to_compare_two_releases():
    out = notes.body("Beta.", "## Fixes\n\n- a", REPO, "1.2.3", "v1.2.2")
    assert out.startswith("Beta.\n\n## Fixes")
    assert out.endswith(f"https://github.com/{REPO}/compare/v1.2.2...v1.2.3\n")
    assert "compare" not in notes.body("", "x", REPO, "1.0.0", None)
    assert notes.NOTHING in notes.body("", "", REPO, "1.0.0", None)
