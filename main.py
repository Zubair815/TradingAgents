"""Noninteractive entry point: python main.py NVDA --date YYYY-MM-DD."""

import sys

from cli.main import app

if __name__ == "__main__":
    app(args=["run", *sys.argv[1:]], prog_name="tradingagents")
