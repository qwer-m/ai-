from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent
from overrides import override


class NoOpProductTelemetryClient(ProductTelemetryClient):
    """本地开发环境使用的空实现：吞掉所有 Chroma 产品遥测事件。"""

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        return None
