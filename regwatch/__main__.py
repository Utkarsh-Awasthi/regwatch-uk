"""Command line: python -m regwatch <command>.

  crawl       Discover and diff changed pages (GOV.UK + legislation.gov.uk)
  summarise   Summarise material changes that don't have a summary yet
  nightly     crawl + summarise (what cron runs every night)
  digest      Build and send weekly digests (SMTP if configured, else data/outbox/)
  serve       Run the web app
  demo        Load the bundled demo documents and users (offline) and build digests
  evals       Run the offline evaluation suite and write evals/report.md
  metrics     Print live impact metrics as JSON
"""
from __future__ import annotations

import argparse
import json
import logging
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="regwatch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crawl")
    c.add_argument("--source", choices=["govuk", "legislation", "all"], default="all")
    c.add_argument("--since", help="YYYY-MM-DD (default: last cursor, else yesterday)")
    c.add_argument("--limit", type=int, default=500)
    c.add_argument("--no-watchlist", action="store_true")
    s = sub.add_parser("summarise")
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--llm", choices=["auto", "on", "off"], default="auto")
    n = sub.add_parser("nightly")
    n.add_argument("--limit", type=int, default=2000)
    n.add_argument("--since", help="YYYY-MM-DD (default: last cursor)")
    d = sub.add_parser("digest")
    d.add_argument("--days", type=int, default=7)
    d.add_argument("--dry-run", action="store_true")
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sub.add_parser("demo")
    e = sub.add_parser("evals")
    e.add_argument("--llm", action="store_true", help="also evaluate Claude summaries (needs ANTHROPIC_API_KEY)")
    sub.add_parser("metrics")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from . import db
    conn = db.connect()

    if args.cmd in ("crawl", "nightly"):
        from . import crawl
        sources = ("govuk", "legislation") if getattr(args, "source", "all") == "all" else (args.source,)
        stats = crawl.run(conn, sources=sources, since=getattr(args, "since", None), limit=args.limit,
                          watchlist=not getattr(args, "no_watchlist", False))
        print(json.dumps({k: v for k, v in stats.__dict__.items() if k != "errors"} | {"errors": len(stats.errors)}))
        for err in stats.errors[:10]:
            print("  error:", err, file=sys.stderr)
    if args.cmd in ("summarise", "nightly"):
        from .summarise import summarise_pending
        use_llm = {"auto": None, "on": True, "off": False}[getattr(args, "llm", "auto")]
        print(json.dumps({"summarised": summarise_pending(conn, limit=getattr(args, "limit", 200), use_llm=use_llm)}))
    elif args.cmd == "digest":
        from .digest import run_weekly
        for r in run_weekly(conn, send=not args.dry_run, days=args.days):
            print(json.dumps(r))
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("regwatch.web:app", host=args.host, port=args.port)
    elif args.cmd == "demo":
        from .demo import load_demo
        print(json.dumps(load_demo(conn), indent=2))
    elif args.cmd == "evals":
        from .config import ROOT
        sys.path.insert(0, str(ROOT))
        from evals.run_evals import main as run_evals
        run_evals(llm=args.llm)
    elif args.cmd == "metrics":
        from .metrics import compute
        print(json.dumps(compute(conn), indent=2, default=str))


if __name__ == "__main__":
    main()
