"""Replay pause and stepping controls."""


class BacktestReplayController:
    def __init__(self) -> None:
        self.is_paused = False
        self.is_step_mode = False

    def pause(self) -> None:
        self.is_paused = True
        self.is_step_mode = False

    def resume(self) -> None:
        self.is_paused = False
        self.is_step_mode = False

    def step(self) -> None:
        self.is_paused = False
        self.is_step_mode = True
