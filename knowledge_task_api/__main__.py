"""Run a local demonstration or an explicitly configured server/worker."""

import argparse
import logging
from pathlib import Path

import uvicorn

from .api import create_app
from .config import Settings
from .store import Store
from .worker import Worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "worker"), nargs="?", default="serve")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--demo", action="store_true", help="public example tokens; localhost only")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "worker":
        store = Store(args.database or Path("data/knowledge.sqlite3"))
        worker = Worker(store)
        try:
            worker.run()
        except KeyboardInterrupt:
            worker.stop()
        return
    if args.demo:
        if args.host != "127.0.0.1":
            parser.error("--demo is restricted to 127.0.0.1")
        settings = Settings.demo(args.database or Path("data/knowledge.sqlite3"))
        logging.info("Local demonstration mode; example tokens are public.")
    else:
        try:
            original = Settings.from_env()
            settings = Settings(args.database or original.database, original.keys)
        except ValueError as error:
            parser.error(str(error))
    uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
