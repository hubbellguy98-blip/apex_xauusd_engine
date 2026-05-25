"""Setup quality classification helpers."""

from src.core.domain.setup_models import SetupQualityTier, SetupType


class InstitutionalSetupQualityClassifier:
    def classify_setup_quality(self, setup_type: SetupType, estimated_rr: float, state_snapshot: object, confirmation_snapshot: object) -> tuple[SetupQualityTier, float]:
        confirmation_score = float(getattr(confirmation_snapshot, "confidence_score", 0.0))
        rr_score = min(estimated_rr / 3.0 * 100.0, 100.0)
        final_score = (confirmation_score * 0.7) + (rr_score * 0.3)
        if estimated_rr < 1.5 or final_score < 60.0:
            return SetupQualityTier.INVALID_SETUP, final_score
        if final_score >= 88.0:
            return SetupQualityTier.ELITE_INSTITUTIONAL, final_score
        if final_score >= 75.0:
            return SetupQualityTier.HIGH_PROBABILITY, final_score
        return SetupQualityTier.STANDARD, final_score
