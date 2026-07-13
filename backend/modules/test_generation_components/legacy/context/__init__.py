from .gate import (
    LegacyGenerationContextGateMixin,
)
from .hybrid import (
    LegacyGenerationContextHybridMixin,
)
from .trace import (
    LegacyGenerationContextTraceMixin,
)


class LegacyGenerationContextMixin(
    LegacyGenerationContextGateMixin,
    LegacyGenerationContextTraceMixin,
    LegacyGenerationContextHybridMixin,
):
    """Context mixin facade composed by gate/trace/hybrid sub-mixins."""

    pass
