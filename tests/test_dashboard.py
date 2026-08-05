import asyncio
import sys; sys.path.insert(0, '.')

from routes.dashboard import _extract_trains, _get_training_or_empty


def test_extract_trains_returns_empty_list_for_string_error_response():
    data = {"success": False, "res": "too frequent, retry after 5s"}

    assert _extract_trains(data) == []


def test_extract_trains_supports_nested_and_top_level_payloads():
    nested = [{"datestr": "2026-07-26"}]
    top_level = [{"datestr": "2026-07-25"}]

    assert _extract_trains({"res": {"trains": nested}}) == nested
    assert _extract_trains({"trains": top_level}) == top_level


def test_get_training_or_empty_tolerates_upstream_failure():
    class FailingDataService:
        async def get_training(self, date):
            raise RuntimeError("502 Bad Gateway")

    result = asyncio.run(
        _get_training_or_empty(FailingDataService(), "2026-07-28")
    )

    assert result == {"res": {"trains": []}}
