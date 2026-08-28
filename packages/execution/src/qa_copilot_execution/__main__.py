"""``python -m qa_copilot_execution`` entry point (S3.1)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
