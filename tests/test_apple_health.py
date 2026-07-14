import sys; sys.path.insert(0, '.')
import apple_health as ah

def test_get_latest_dates_returns_dict():
    result = ah.get_latest_dates()
    assert isinstance(result, dict)
    assert all(isinstance(k, str) for k in result)
    assert all(isinstance(v, str) or v is None for v in result.values())

def test_get_latest_dates_filtered():
    result = ah.get_latest_dates("HKQuantityTypeIdentifierStepCount")
    assert "HKQuantityTypeIdentifierStepCount" in result
    assert result["HKQuantityTypeIdentifierStepCount"] is not None
    assert len(result["HKQuantityTypeIdentifierStepCount"]) == 10
