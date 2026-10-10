"""Compatibility alias: this module moved to octopus.prompts."""
import sys

from octopus import prompts as _module

sys.modules[__name__] = _module
