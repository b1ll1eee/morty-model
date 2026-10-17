import sys

from morty import cli


def test_help_and_version(capsys):
    assert cli.main(["--help"]) == 0
    assert "morty <คำสั่ง>" in capsys.readouterr().out
    assert cli.main(["version"]) == 0
    assert "morty" in capsys.readouterr().out


def test_unknown_command(capsys):
    assert cli.main(["nope"]) == 2
    assert "ไม่รู้จักคำสั่ง" in capsys.readouterr().err


def test_dispatch_sets_argv_and_defaults(monkeypatch):
    seen = {}

    class Fake:
        @staticmethod
        def main():
            seen["argv"] = list(sys.argv)
            return 0

    monkeypatch.setattr(cli, "import_module", lambda name: Fake if name in ("morty.chat", "morty.ui") else None)
    assert cli.main([]) == 0
    assert seen["argv"] == ["morty ui", "--profile", "dev"]
    assert cli.main(["chat", "--profile", "general"]) == 0
    assert seen["argv"] == ["morty chat", "--profile", "general"]
