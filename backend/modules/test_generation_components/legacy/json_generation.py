from __future__ import annotations

import sys

from . import json_generation_impl as _impl

# Compatibility alias: historical imports patch this module path directly.
# Point it at the implementation module so monkeypatches hit the live globals.
sys.modules[__name__] = _impl
