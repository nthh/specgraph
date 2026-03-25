"""Allow running as `python -m specgraph`."""
from specgraph import main
import sys

sys.exit(main() or 0)
