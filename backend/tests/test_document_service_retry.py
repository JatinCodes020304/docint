from app.services.document_service import _is_validation_improvement


def V(statuses):
    return {"checks": [{"status": s} for s in statuses]}


def test_audit_retry_cannot_win_by_nulling_failed_checks():
    original = V(["PASS", "PASS", "FAIL"])
    nulled = V(["PASS", "NOT_APPLICABLE", "NOT_APPLICABLE"])
    assert _is_validation_improvement(original, nulled) is False


def test_audit_retry_can_win_when_same_applicability_reduces_failures():
    original = V(["PASS", "PASS", "FAIL"])
    corrected = V(["PASS", "PASS", "PASS"])
    assert _is_validation_improvement(original, corrected) is True
