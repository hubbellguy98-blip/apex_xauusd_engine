"""Risk/reward validation."""


class AsymmetricOpportunityValidator:
    def __init__(self, minimum_rr: float = 1.5) -> None:
        self._minimum_rr = minimum_rr

    def validate_target_feasibility(self, setup, state_snapshot) -> tuple[bool, str]:
        if setup.estimated_rr < self._minimum_rr:
            return False, "RISK_REWARD_BELOW_MINIMUM"
        return True, "OK"
