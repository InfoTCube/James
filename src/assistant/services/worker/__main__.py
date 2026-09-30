"""Scheduler: runs every module's collector on its schedule.

python -m assistant.services.worker                   # run forever
python -m assistant.services.worker --run-once NAME   # run one collector now
"""

import argparse
import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

from assistant.core.collector import run_collector
from assistant.core.config import load_config
from assistant.core.db import get_engine
from assistant.modules import COLLECTORS


def main() -> int:
    parser = argparse.ArgumentParser(prog="assistant.services.worker")
    parser.add_argument("--run-once", metavar="NAME", choices=[c.name for c in COLLECTORS])
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    config, engine = load_config(), get_engine()

    if args.run_once:
        collector = next(c for c in COLLECTORS if c.name == args.run_once)
        return 0 if run_collector(collector, engine) else 1

    scheduler = BlockingScheduler(timezone=config.tz)
    for c in COLLECTORS:
        # coalesce + max_instances=1: a slow/missed run never piles up
        scheduler.add_job(
            run_collector, c.schedule, args=[c, engine], id=c.name, coalesce=True, max_instances=1
        )
    logging.info("worker started with %d collectors", len(COLLECTORS))
    scheduler.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
