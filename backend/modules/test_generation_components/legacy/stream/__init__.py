from .generation import (
    LegacyGenerationStreamGenerationMixin,
)


class LegacyGenerationStreamMixin(LegacyGenerationStreamGenerationMixin):
    """Stream mixin facade. Generation implementation lives in stream_generation."""

    pass
