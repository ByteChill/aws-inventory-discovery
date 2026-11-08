import argparse
import logging
import sys

from app.core.logging import setup_logging

logger = logging.getLogger(__name__)


def run_worker() -> int:
    from app.worker.main import Worker

    return Worker.from_settings().run()


def run_doctor() -> int:
    from app.core.config import get_settings
    from app.doctor import format_table, run_checks

    for noisy in ("httpx", "botocore"):  # the table is the output, not the request log
        logging.getLogger(noisy).setLevel(logging.WARNING)
    checks = run_checks(get_settings())
    print(format_table(checks))
    failed = [c for c in checks if not c.ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


COMMANDS = {"worker": run_worker, "doctor": run_doctor}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    setup_logging()
    return COMMANDS[args.command]()


if __name__ == "__main__":
    sys.exit(main())
