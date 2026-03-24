from modules.test_generation_components.legacy.context.gate import (
    LegacyGenerationContextGateMixin,
)
from modules.test_generation_components.legacy.context.hybrid import (
    LegacyGenerationContextHybridMixin,
)
from modules.test_generation_components.legacy.context.trace import (
    LegacyGenerationContextTraceMixin,
)


class LegacyGenerationContextMixin(
    LegacyGenerationContextGateMixin,
    LegacyGenerationContextTraceMixin,
    LegacyGenerationContextHybridMixin,
):
    """Context mixin facade composed by gate/trace/hybrid sub-mixins."""

    pass
