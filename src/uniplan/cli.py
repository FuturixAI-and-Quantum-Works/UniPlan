import argparse
import os
from pathlib import Path

from uniplan.server import build_server


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="uniplan-mcp",
        description="UniPlan MCP server (stdio): turns a tender or PRD into a build plan for UniCAD.",
        epilog="Environment: UNIPLAN_DIR plan folder (default ./plan); UNICAD_CATALOG UniCAD catalog folder to check equipment against.",
    )
    p.add_argument("--plan-dir", type=Path, help="plan folder (overrides UNIPLAN_DIR)")
    args = p.parse_args(argv)
    root = args.plan_dir or Path(os.environ.get("UNIPLAN_DIR") or "./plan")
    build_server(root.expanduser().resolve()).run("stdio")


if __name__ == "__main__":
    main()
