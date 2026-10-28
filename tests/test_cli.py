"""CLI smoke tests: commands must work without an explicit --universe.

The docker `app` service runs `trading_expert.cli watch` with no flags; a None
universe used to crash with KeyError: None.
"""

import pytest
from click.testing import CliRunner

from trading_expert.cli import cli


class TestDefaultUniverse:
    @pytest.mark.parametrize("command, label", [
        (["watch"], "Semiconductor & Tech"),
        (["daily"], "Semiconductor & Tech"),
        (["portfolio"], "Semiconductor & Tech"),
        (["backtest"], "Semiconductor & Tech"),
    ])
    def test_defaults_to_semiconductor(self, command, label):
        result = CliRunner().invoke(cli, command)
        assert result.exit_code == 0, result.output
        assert label in result.output

    def test_explicit_universe_still_honored(self):
        result = CliRunner().invoke(cli, ["watch", "--universe", "bmv"])
        assert result.exit_code == 0
        assert "BMV (Mexican Stocks)" in result.output

    def test_all_universes(self):
        result = CliRunner().invoke(cli, ["daily", "--universe", "all"])
        assert result.exit_code == 0
        assert "ALL universes" in result.output
