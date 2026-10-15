import pytest

from mog_client import launchargs


@pytest.mark.parametrize(
    ("typed", "tags"),
    [
        ("", []),
        ("-windowed -nosound", ["-windowed", "-nosound"]),
        ("-res 1280 720 -windowed --nosound", ["-res 1280 720", "-windowed", "--nosound"]),
        ("--width=1280 --height 720", ["--width=1280", "--height 720"]),
        ("intro.avi -w", ["intro.avi", "-w"]),
        ("-x -5 -y", ["-x -5", "-y"]),
        ("-title 'My Game' -w", ["-title 'My Game'", "-w"]),
        ("-w -w -nosound", ["-w", "-nosound"]),
        ("-title 'oops -w", ["-title 'oops", "-w"]),
    ],
)
def test_typed_arguments_split_into_tags_at_each_word_that_starts_with_a_dash(typed, tags):
    assert launchargs.split_tags(typed) == tags


def test_tags_become_the_words_after_the_executable_and_a_quote_left_open_is_an_error():
    assert launchargs.words(["-res 1280 720", "-title 'My Game'", "--nosound"]) == ["-res", "1280", "720", "-title", "My Game", "--nosound"]
    with pytest.raises(ValueError):
        launchargs.words(["-x 'oops"])


def test_what_is_kept_as_tags_whatever_it_was():
    assert launchargs.as_tags(["-a", "-a", "", " -b", "-c"]) == ["-a", "-c"]
    assert launchargs.as_tags("-a -b 1") == ["-a", "-b 1"]
    assert launchargs.as_tags(None) == [] and launchargs.as_tags(3) == []
    assert len(launchargs.as_tags([f"-t{n}" for n in range(80)])) == launchargs.MAX_TAGS


@pytest.mark.parametrize(
    ("base", "ours", "theirs", "merged"),
    [
        (["-a"], ["-a"], ["-a"], ["-a"]),
        (["-a"], ["-a", "-c"], ["-a"], ["-a", "-c"]),  # added here
        (["-a"], ["-a"], ["-a", "-d"], ["-a", "-d"]),  # added there
        (["-a", "-b"], ["-a", "-b", "-c"], ["-a", "-d"], ["-a", "-d", "-c"]),  # one adds, the other adds and removes
        (["-a", "-b"], ["-b"], ["-a", "-b"], ["-b"]),  # removed here
        (["-a", "-b"], ["-a", "-b"], ["-a"], ["-a"]),  # removed there
        (["-a"], ["-a", "-x"], ["-a", "-x"], ["-a", "-x"]),  # both added the same: once
        ([], ["-mine"], [], ["-mine"]),
        (["-a"], ["-a"], [], []),  # all deleted there
    ],
)
def test_two_editors_are_merged_by_tag(base, ours, theirs, merged):
    assert launchargs.merge(base, ours, theirs) == merged
