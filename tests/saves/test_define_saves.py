"""Define saves: the flows between the window and the server's save rules, with a stand-in window that records what it shows."""

from types import SimpleNamespace

import pytest
from test_saves_ui import FakeWin, Server, ui  # noqa: F401 - the fixture

from mog_client.gui.definesaves import DefineSaves
from mog_client.gui.saves_ui import SaveSync, where_is
from mog_client.saves import rules as save_rules


class Page:
    """What the window's pages offer the flow: they are told the new rules or version."""

    def __init__(self, rules=None):
        self.rules = rules or save_rules.Rules()
        self.versions = []

    def set_rules(self, rules):
        self.rules = rules

    def set_version(self, version):
        self.versions.append(version)


@pytest.fixture
def flow(ui):  # noqa: F811
    win = ui.win
    win.saves = SaveSync(win)
    win.shown = []
    win.browsed_any = []
    win.show_define_saves = lambda f, rec, ctx, rules: win.shown.append(("rules", rules))
    win.show_save_files = lambda f, rec, ctx, version, rules, parent=None: win.shown.append(("files", version))
    win.browse_any = lambda start, on_pick, text="": win.browsed_any.append((start, on_pick))
    ui.flow = DefineSaves(win)
    ui.ctx = None
    ui.page = Page()

    def context():
        found = []
        win.saves.with_context(ui.rec, found.append, ask=True)
        return found[0]

    ui.context = context
    return ui


def test_the_rules_the_server_keeps_are_what_the_page_opens_with(flow):
    flow.server.rules = {"blacklist": ["game/cache"], "whitelist": ["game/extra.ini"]}

    flow.flow.start(flow.rec)

    kind, rules = flow.win.shown[0]
    assert kind == "rules" and (rules.blacklist, rules.whitelist) == (["game/cache"], ["game/extra.ini"])


def test_a_folder_picked_in_the_game_is_asked_about_and_then_blacklisted_on_the_server(flow):
    (flow.install / "shaders").mkdir()
    ctx = flow.context()

    flow.flow.add(flow.rec, ctx, flow.page)
    start, picked = flow.win.browsed_any[0]
    picked(str(flow.install / "shaders"))
    title, options, on_choose = flow.win.choices[-1]
    on_choose(dict((label.split(" (")[0], v) for label, v in options)["Never back it up"])

    assert flow.server.rules == {"blacklist": ["game/shaders"], "whitelist": []}
    assert flow.page.rules.blacklist == ["game/shaders"]
    assert "left out of the backup" in flow.win.notes[-1]


def test_a_path_with_no_name_in_a_save_archive_is_refused_with_a_message(flow):
    ctx = flow.context()

    flow.flow.add(flow.rec, ctx, flow.page)
    flow.win.browsed_any[0][1]("/usr/share/x")

    assert flow.win.messages and "is not in the game's folder" in flow.win.messages[-1][1]
    assert flow.server.rules == {"blacklist": [], "whitelist": []}


def test_taking_a_rule_off_tells_the_server_and_the_page(flow):
    flow.server.rules["blacklist"] = ["game/cache"]
    ctx = flow.context()

    flow.flow.forget(flow.rec, ctx, flow.page, ["game/cache"])

    assert flow.server.rules["blacklist"] == [] and flow.page.rules.blacklist == []


def test_deleting_files_of_a_version_asks_first_and_the_page_gets_the_version_that_is_left(flow):
    flow.server.add_foreign_version({"game/slot.sav": b"s", "game/cache/a.bin": b"c"})
    ctx = flow.context()
    version = flow.server.get_save(1)

    flow.flow.delete_files(flow.rec, ctx, flow.page, version, ["game/cache/a.bin"])
    assert flow.server.removed_files == []  # nothing before the answer
    text, yes, _ = flow.win.asks[-1]
    assert "cannot be undone" in text
    yes()

    assert flow.server.removed_files == [(1, ["game/cache/a.bin"])]
    assert flow.page.versions and "taken out of the version" in flow.win.notes[-1]


def test_the_folders_question_shows_where_each_folder_is_on_this_disk(flow, monkeypatch):
    """`users/USER/Documents/Game` is not a place anywhere: the person looks for it in the prefix on their disk."""
    from mog_client.saves.state import SaveState, save_state

    prefix = flow.tmp / "home/Faugus/g"
    (prefix / "pfx/drive_c/users/steamuser/Documents/Game").mkdir(parents=True)
    save_state(7, SaveState(prefix=str(prefix), prefix_source="faugus"))

    shown = where_is(flow.rec, "users/USER/Documents/Game")

    assert shown == "~/Faugus/g/pfx/drive_c/users/steamuser/Documents/Game"
    assert where_is(flow.rec, "game/saves") == str(flow.install / "saves")


def test_a_pattern_typed_in_is_checked_then_asked_about_and_blacklisted_on_the_server(flow):
    flow.win.asked_text = []
    flow.win.ask_text = lambda title, text, default, on_done: flow.win.asked_text.append(on_done)
    ctx = flow.context()

    flow.flow.add_pattern(flow.rec, ctx, flow.page)
    typed = flow.win.asked_text[0]
    typed("re:(")  # does not compile: said at once, nothing asked
    assert flow.win.messages and flow.win.choices == []
    typed("*.png")
    title, options, on_choose = flow.win.choices[-1]
    on_choose(dict((label.split(" (")[0], v) for label, v in options)["Never back them up"])

    assert flow.server.rules == {"blacklist": ["*.png"], "whitelist": []}
    assert flow.page.rules.blacklist == ["*.png"]
