import typer
from typer.testing import CliRunner

from src.config import config_cli

app = typer.Typer()
app.command()(config_cli)

runner = CliRunner()


def test_config_flag_prints_only_that_value():
    result = runner.invoke(app, ["--flask-port"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "5007"


def test_config_all_prints_key_value_lines():
    result = runner.invoke(app, ["--all"])
    assert result.exit_code == 0
    assert "project_name=trainspotter" in result.stdout.splitlines()
    assert "flask_port=5007" in result.stdout.splitlines()


def test_config_without_flag_fails():
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "Error: No config key specified" in result.output
