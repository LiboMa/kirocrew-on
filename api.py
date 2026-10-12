"""Compatibility alias: this module moved to octopus.gateway_client."""
import sys

from octopus import gateway_client as _module

sys.modules[__name__] = _module
