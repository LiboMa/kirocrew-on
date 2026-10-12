"""Compatibility alias: this module moved to octopus.sources."""
import sys

from octopus import sources as _module

sys.modules[__name__] = _module
