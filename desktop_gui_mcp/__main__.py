"""CLI entry point for the Desktop GUI MCP server."""

from .server import run_server


def main() -> None:
    """Execute the Desktop GUI MCP server."""

    run_server()


if __name__ == "__main__":
    main()
