"""Apply the Journl schema migration using the configured PostgreSQL connection."""

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT if (ROOT / "db.py").is_file() else ROOT / "app"
sys.path.insert(0, str(APP_DIR))

from alembic import command
from alembic.config import Config
from db import database_url


def main():
    parser = argparse.ArgumentParser(description="Journl PostgreSQL-Schema migrieren")
    parser.add_argument("command", choices=("upgrade", "current"), nargs="?", default="upgrade")
    args = parser.parse_args()
    config = Config(str(APP_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(APP_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url())
    if args.command == "upgrade":
        command.upgrade(config, "head")
    else:
        command.current(config)


if __name__ == "__main__":
    main()
