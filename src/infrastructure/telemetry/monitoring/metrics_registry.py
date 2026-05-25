"""Global metric registry."""


class GlobalMetricsRegistry:
    def __init__(self) -> None:
        self.metrics: dict[str, list[float]] = {}

    def record(self, key: str, value: float) -> None:
        self.metrics.setdefault(key, []).append(value)
