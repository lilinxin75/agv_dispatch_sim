"""入口：``python -m rcs_demo [--check] [--port N] [--seed N] [--fast]``。"""

from __future__ import annotations

import argparse
import sys

from rcs_demo.config import Config
from rcs_demo.sim.engine import Engine


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rcs_demo",
        description="RCS multi-vehicle grid dispatch demo",
    )
    p.add_argument("--check", action="store_true",
                   help="run self-test and exit (no server)")
    p.add_argument("--port", type=int, default=None,
                   help=f"server port (default {Config.port})")
    p.add_argument("--host", type=str, default=None,
                   help=f"bind host (default {Config.host})")
    p.add_argument("--seed", type=int, default=None,
                   help="random seed for reproducible scenarios")
    p.add_argument("--fast", action="store_true",
                   help="speed up the simulation clock")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    cfg = Config.from_env()
    if args.port is not None:
        cfg.port = args.port
    if args.host is not None:
        cfg.host = args.host
    if args.fast:
        cfg.fast = True

    engine = Engine(cfg, seed=args.seed)

    if args.check:
        for line in engine.self_check():
            print(f"  {line}")
        print("OK: grid layout / planner / engine self-test passed")
        return 0

    import uvicorn

    from rcs_demo.api.routes import create_app

    uvicorn.run(create_app(engine), host=cfg.host, port=cfg.port,
                log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
