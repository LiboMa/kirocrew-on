"""Compatibility alias: this module moved to octopus.documents."""
import sys

from octopus import documents as _module

sys.modules[__name__] = _module
