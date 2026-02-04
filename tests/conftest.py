"""Pytest fixtures and hooks. Mocks src.values so CI can run without values.py."""

import sys
from types import ModuleType

# Mock values module before any test imports src.config (e.g. test_config.py)
if "src.values" not in sys.modules:
    _values = ModuleType("src.values")
    _values.GMAPS_API_KEY = ""
    sys.modules["src.values"] = _values
