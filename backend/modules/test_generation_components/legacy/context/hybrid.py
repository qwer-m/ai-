from __future__ import annotations

import sys

from . import hybrid_impl_clean as _impl

# Compatibility alias for historical monkeypatch targets.
sys.modules[__name__] = _impl
