from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .adapters import AkashaAgentAdapter, ReplayAdapter
from .orchestration import run_demo
from .reporting import ReportStore


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tainan-demo", description="臺南古蹟導覽偏見測試展示系統")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="執行一次完整展示並匯出報告")
    run.add_argument("--project-root", type=Path, default=_default_root())
    run.add_argument("--output", type=Path, default=None)
    run.add_argument("--mode", choices=("replay", "live"), default="replay")
    run.add_argument("--model", default="gemini:gemini-2.5-flash")
    serve = sub.add_parser("serve", help="啟動本機 Web 展示頁")
    serve.add_argument("--project-root", type=Path, default=_default_root())
    serve.add_argument("--output", type=Path, default=None)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8764)
    listing = sub.add_parser("list", help="列出已保存展示結果")
    listing.add_argument("--project-root", type=Path, default=_default_root())
    listing.add_argument("--output", type=Path, default=None)
    return parser


def _store(args) -> ReportStore:
    return ReportStore(args.output or (args.project_root / "runs"))


def _print_run(console: Console, run, artifacts) -> None:
    console.print(Panel.fit("[bold]臺南日治時期古蹟導覽偏見測試[/bold]\n固定題測知道；情境題測做到。", border_style="cyan"))
    table = Table(show_header=True, header_style="bold")
    table.add_column("指標")
    table.add_column("結果", justify="right")
    table.add_row("固定題原則通過率", f"{run.summary['fixed_principle_pass_rate']:.0%}")
    table.add_row("情境題框架偏差率", f"{run.summary['scenario_bias_rate']:.0%}")
    table.add_row("執行錯誤", str(run.summary["errors"]))
    console.print(table)
    console.print(f"JSON  [link=file://{artifacts.json_path}]{artifacts.json_path}[/link]")
    console.print(f"HTML  [link=file://{artifacts.html_path}]{artifacts.html_path}[/link]")


def run_cli(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    console = Console()
    if args.command == "run":
        if args.mode == "replay":
            adapter = ReplayAdapter(args.project_root / "data" / "fixtures" / "responses.jsonl")
        else:
            adapter = AkashaAgentAdapter(model=args.model)
        run = asyncio.run(run_demo(args.project_root, adapter))
        artifacts = _store(args).save(run)
        _print_run(console, run, artifacts)
        return 0 if run.summary["errors"] == 0 else 2
    if args.command == "list":
        runs = _store(args).list_runs()
        if not runs:
            console.print("尚無展示結果。")
        for item in runs:
            console.print(f"{item['created_at']}  {item['run_id']}")
        return 0
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be between 1 and 65535")
    import uvicorn

    from .web import create_app

    console.print(f"[bold cyan]展示頁已啟動[/bold cyan] http://{args.host}:{args.port}")
    uvicorn.run(create_app(args.project_root, runs_dir=args.output), host=args.host, port=args.port)
    return 0


def main() -> None:
    raise SystemExit(run_cli())
