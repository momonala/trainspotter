import tomllib
from pathlib import Path

import typer

_config_file = Path(__file__).parent.parent / "pyproject.toml"
with _config_file.open("rb") as f:
    _config = tomllib.load(f)

_project_config = _config["project"]
_tool_config = _config["tool"]["config"]

PROJECT_NAME = _project_config["name"]
PROJECT_VERSION = _project_config["version"]
FLASK_PORT = _tool_config["flask_port"]
SPYGLASS_HOST = _tool_config["spyglass_host"]
VBB_API_BASE = _tool_config["vbb_api_base"].rstrip("/")


# fmt: off
def config_cli(
    show_all: bool = typer.Option(False, "--all", help="Show all configuration values"),
    project_name: bool = typer.Option(False, "--project-name", help=PROJECT_NAME),
    project_version: bool = typer.Option(False, "--project-version", help=PROJECT_VERSION),
    flask_port: bool = typer.Option(False, "--flask-port", help=str(FLASK_PORT)),
    spyglass_host: bool = typer.Option(False, "--spyglass-host", help=SPYGLASS_HOST),
    vbb_api_base: bool = typer.Option(False, "--vbb-api-base", help=VBB_API_BASE),
) -> None:
# fmt: on
    values = [
        ("project_name", project_name, PROJECT_NAME),
        ("project_version", project_version, PROJECT_VERSION),
        ("flask_port", flask_port, FLASK_PORT),
        ("spyglass_host", spyglass_host, SPYGLASS_HOST),
        ("vbb_api_base", vbb_api_base, VBB_API_BASE),
    ]

    if show_all:
        for key, _, value in values:
            typer.echo(f"{key}={value}")
        return

    for _, is_set, value in values:
        if is_set:
            typer.echo(value)
            return

    typer.secho("Error: No config key specified. Use --help to see available options.", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def main():
    typer.run(config_cli)


if __name__ == "__main__":
    main()
