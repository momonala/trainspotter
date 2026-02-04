import json
import tomllib
from pathlib import Path

import typer

from .values import GMAPS_API_KEY

_config_file = Path(__file__).parent.parent / "pyproject.toml"
with _config_file.open("rb") as f:
    _config = tomllib.load(f)

_project_config = _config["project"]
_tool_config = _config["tool"]["config"]

FLASK_PORT = _tool_config["flask_port"]

_json_config_file = Path(__file__).parent.parent / "config.json"
with _json_config_file.open("r") as f:
    _json_config = json.load(f)

stations = _json_config["stations"]
walk_time_buffer = _json_config["walk_time_buffer"]
location = _json_config["location"]
update_interval_min = _json_config["update_interval_min"]
min_departure_time_min = _json_config["min_departure_time_min"]
gmaps_api_key = GMAPS_API_KEY


# fmt: off
def config_cli(
    all: bool = typer.Option(False, "--all", help="Show all configuration values"),
    project_name: bool = typer.Option(False, "--project-name", help=_project_config['name']),
    project_version: bool = typer.Option(False, "--project-version", help=_project_config['version']),
    flask_port: bool = typer.Option(False, "--flask-port", help=str(FLASK_PORT)),
) -> None:
# fmt: on
    if all:
        typer.echo(f"project_name={_project_config['name']}")
        typer.echo(f"project_version={_project_config['version']}")
        typer.echo(f"flask_port={FLASK_PORT}")
        return

    param_map = {
        project_name: _project_config["name"],
        project_version: _project_config["version"],
        flask_port: FLASK_PORT,
    }

    for is_set, value in param_map.items():
        if is_set:
            typer.echo(value)
            return

    typer.secho("Error: No config key specified. Use --help to see available options.", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def main():
    typer.run(config_cli)


if __name__ == "__main__":
    main()
