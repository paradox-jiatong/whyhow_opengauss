"""Compatibility package for the openGauss WhyHow API example.

The upstream example keeps modules at the repository root while the code imports
them through ``whyhow_api.*``. Expose the repository root as part of this package
so both layouts keep working during the migration to a proper package layout.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
__path__.append(str(_ROOT))

__version__ = "0.1.0"
