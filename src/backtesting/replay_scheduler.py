"""Chronological replay stream scheduler."""


class BacktestReplayScheduler:
    def __init__(self) -> None:
        self._buffers_registry = {}

    def register_stream_buffer(self, key: str, buffer_pool) -> None:
        self._buffers_registry[key] = buffer_pool

    def locate_next_chronological_track(self) -> str | None:
        candidates = []
        for key, buffer_pool in self._buffers_registry.items():
            if hasattr(buffer_pool, "has_next") and not buffer_pool.has_next():
                continue
            if hasattr(buffer_pool, "peek_next_node"):
                node = buffer_pool.peek_next_node()
                candidates.append((node["timestamp"], key))
            else:
                candidates.append((0, key))
        if not candidates:
            return None
        return min(candidates, key=lambda item: item[0])[1]
