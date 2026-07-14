import sys; sys.path.insert(0, '.')
from data_service import DataFreshnessService

def test_freshness_context_has_required_keys():
    svc = DataFreshnessService()
    ctx = svc.get_freshness_context()
    assert "ah_overall_latest" in ctx
    assert "xunji_latest" in ctx
    assert "ah_days_ago" in ctx
    assert "ah_status" in ctx
    assert "metrics" in ctx
    assert isinstance(ctx["metrics"], dict)
