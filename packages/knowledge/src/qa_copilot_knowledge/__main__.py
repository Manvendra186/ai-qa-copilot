"""`python -m qa_copilot_knowledge` entry point (see cli.py)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
