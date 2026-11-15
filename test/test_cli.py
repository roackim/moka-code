"""The ``moka`` command line."""

import pytest

from moka_code import __version__
from moka_code.main import main


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_prints_and_exits(flag, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["moka", flag])
    monkeypatch.setenv("MOKA_TERMINAL", "123")          # works inside /terminal too
    with pytest.raises(SystemExit) as exit_:
        main()
    assert exit_.value.code == 0
    assert capsys.readouterr().out.strip() == f"moka {__version__}"
