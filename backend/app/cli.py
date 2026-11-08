import argparse
import logging
import sys

from app.core.logging import setup_logging

logger = logging.getLogger(__name__)

NOT_IMPLEMENTED = 2


def run_worker() -> int:
    logger.error("worker is not implemented yet")
    return NOT_IMPLEMENTED


def run_doctor() -> int:
    logger.error("doctor is not implemented yet")
    return NOT_IMPLEMENTED


COMMANDS = {"worker": run_worker, "doctor": run_doctor}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    setup_logging()
    return COMMANDS[args.command]()


if __name__ == "__main__":
    sys.exit(main())
