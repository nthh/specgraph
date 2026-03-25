"""Allow running as `python -m trk`."""
from trk import main
import sys

sys.exit(main() or 0)
