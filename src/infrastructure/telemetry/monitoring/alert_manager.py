"""Operational alert manager."""


class OperationsAlertManager:
    def __init__(self) -> None:
        self.alerts: list[str] = []

    def emit(self, message: str) -> None:
        self.alerts.append(message)
