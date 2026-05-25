"""Component scorers for trade ranking."""


class StructureLiquidityScorer:
    def evaluate_structural_quality(self, setup, state_snapshot) -> float:
        return min(max(setup.confidence_score, 0.0), 100.0)

    def evaluate_liquidity_quality(self, setup, state_snapshot) -> float:
        return 85.0 if "LIQUIDITY" in setup.setup_type.value else 70.0


class VolatilityMomentumScorer:
    def evaluate_momentum_quality(self, confirmation) -> float:
        return min(max(confirmation.confidence_score, 0.0), 100.0)

    def evaluate_volatility_quality(self, state_snapshot) -> float:
        ratio = getattr(state_snapshot.regime, "volatility_ratio", 1.0)
        return max(0.0, min(100.0, 100.0 - abs(ratio - 1.2) * 30.0))


class RRExecutionScorer:
    def evaluate_rr_quality(self, setup) -> float:
        return max(0.0, min(100.0, setup.estimated_rr / 3.0 * 100.0))

    def evaluate_execution_efficiency(self, state_snapshot) -> float:
        spread = getattr(state_snapshot.market, "current_spread", 0.0) * 10.0
        return max(0.0, 100.0 - spread * 20.0)
