from mog_client import winecfg

USER_REG = (
    "WINE REGISTRY Version 2\n"
    ";; All keys relative to \\\\User\\\\S-1-5-21\n\n"
    "#arch=win64\n\n"
    '[Software\\\\Wine] 1700000000\n#time=1d9\n"Version"="win7"\n"MouseWarpOverride"="enable"\n\n'
    '[Software\\\\Wine\\\\DllOverrides] 1700000001\n#time=1d9\n"d3d9"="native,builtin"\n"blob"=hex:01,02,\\\n  03,04\n"gone"=-\n\n'
    '[Software\\\\Wine\\\\Drivers] 1700000002\n"Audio"="alsa"\n\n'
    '[Software\\\\Wine\\\\Explorer] 1700000003\n"Desktop"="Default"\n"Other"="x"\n\n'
    '[Software\\\\Wine\\\\AppDefaults\\\\game.exe] 1700000004\n"Version"="winxp"\n\n'
    '[Software\\\\Microsoft\\\\Windows] 1700000005\n"Not"="wine"\n'
)

EXPORTED = (
    "Windows Registry Editor Version 5.00\n\n"
    '[HKEY_CURRENT_USER\\Software\\Wine]\n"Version"="win7"\n\n'
    '[HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides]\n"d3d9"="native,builtin"\n"blob"=hex:01,02,\\\n  03,04\n\n'
    '[HKEY_CURRENT_USER\\Software\\Wine\\Explorer]\n"Desktop"="Default"\n\n'
    '[HKEY_CURRENT_USER\\Software\\Wine\\AppDefaults\\game.exe]\n"Version"="winxp"\n'
)


def test_only_what_winecfg_sets_is_exported_from_the_prefixs_registry():
    assert winecfg.export_settings(USER_REG) == EXPORTED


def test_an_export_survives_the_import_filter_unchanged():
    assert winecfg.clean_import(EXPORTED) == EXPORTED


def test_a_registry_without_settings_exports_nothing():
    assert winecfg.export_settings('WINE REGISTRY Version 2\n\n[Software\\\\Wine\\\\Drivers] 1\n"Audio"="alsa"\n') == ""
    assert winecfg.export_settings("") == ""


def test_the_import_drops_other_keys_and_every_deletion_because_the_text_comes_from_a_server():
    hostile = (
        "Windows Registry Editor Version 5.00\n\n"
        '[HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows\\CurrentVersion\\Run]\n"x"="calc.exe"\n\n'
        '[HKEY_CURRENT_USER\\Software\\Wine\\Drivers]\n"Audio"="x"\n\n'
        "[-HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides]\n\n"
        '[HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides]\n"a"=-\n"b"="native"\n\n'
        '[HKEY_CURRENT_USER\\Software\\WineX\\DllOverrides]\n"c"="native"\n'
    )

    assert winecfg.clean_import(hostile) == (
        'Windows Registry Editor Version 5.00\n\n[HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides]\n"b"="native"\n'
    )


def test_the_registry_file_is_found_in_a_wine_prefix_and_one_level_down_in_a_proton_one(tmp_path):
    assert winecfg.find_user_reg(tmp_path) is None
    (tmp_path / "pfx").mkdir()
    (tmp_path / "pfx" / "user.reg").write_text("")
    assert winecfg.find_user_reg(tmp_path) == tmp_path / "pfx" / "user.reg"
    (tmp_path / "user.reg").write_text("")
    assert winecfg.find_user_reg(tmp_path) == tmp_path / "user.reg"


H = "Windows Registry Editor Version 5.00\n\n"
DLL = "[HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides]\n"


def test_the_undo_puts_back_old_values_and_deletes_new_ones_and_skips_what_did_not_change():
    before = H + DLL + '"msvcp140"="native,builtin"\n"d3d9"="builtin"\n'
    after = H + DLL + '"msvcp140"="native,builtin"\n"d3d9"="native"\n"dxgi"="native"\n'
    undo = winecfg.undo_text(before, after)
    assert undo == H + DLL + '"d3d9"="builtin"\n"dxgi"=-\n'
    assert winecfg.undo_text(after, after) == ""
    assert winecfg.undo_text("", "") == ""


def test_the_undo_only_covers_what_the_settings_cover():
    after = H + DLL + '"dxgi"="native"\n\n[HKEY_CURRENT_USER\\Software\\Other]\n"x"="y"\n'
    assert "Other" not in winecfg.undo_text("", after)


def test_two_undos_merge_with_the_older_value_winning_and_deletions_kept():
    older = H + DLL + '"d3d9"="builtin"\n"dxgi"=-\n'
    newer = H + DLL + '"d3d9"="native"\n"extra"=-\n'
    merged = winecfg.merge_undo(older, newer)
    assert '"d3d9"="builtin"' in merged and '"d3d9"="native"' not in merged
    assert '"dxgi"=-' in merged and '"extra"=-' in merged
