"""Latest dates use index seeks without scanning every health sample."""

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest


@pytest.mark.parametrize('type_filter', [None, 'metric-b'])
def test_latest_dates_work_is_bounded_by_types_not_sample_count(isolated_health_db, monkeypatch, type_filter):
    ah = isolated_health_db
    base = datetime(2026, 6, 1)
    rows = [dict(type=metric,
                 start_date=(base + timedelta(seconds=index)).strftime('%Y-%m-%d %H:%M:%S +0800'),
                 end_date=(base + timedelta(seconds=index)).strftime('%Y-%m-%d %H:%M:%S +0800'),
                 value=1, unit='count', source_name='synthetic')
            for metric in ['metric-a', 'metric-b', 'metric-c'] for index in range(4000)]
    ah._bulk_insert(rows, [])
    original = ah._conn
    steps = []

    def budget():
        steps.append(1)
        # A deterministic SQLite VM budget, not a wall-clock timing assertion.
        return int(len(steps) > 100)

    @contextmanager
    def bounded_connection():
        with original() as conn:
            conn.set_progress_handler(budget, 100)
            try:
                yield conn
            finally:
                conn.set_progress_handler(None, 0)

    monkeypatch.setattr(ah, '_conn', bounded_connection)
    expected = {metric: '2026-06-01' for metric in ['metric-a', 'metric-b', 'metric-c']
                if type_filter is None or metric == type_filter}
    assert ah.get_latest_dates(type_filter) == expected


def test_indexed_latest_dates_preserve_prefix_and_empty_store_semantics(isolated_health_db):
    ah = isolated_health_db
    assert ah.get_latest_dates() == {}
    assert ah.get_latest_dates('missing') == {'missing': None}
    by_type = {
        'normal': ['2026-01-01 23:00:00 +0800', '2026-01-02 01:00:00 +0800'],
        'broken': ['not-a-date 08:00:00', '2026-10-01 08:00:00'],
        'empty': [''],
        '': ['0001-01-01 00:00:00 +0800'],
        "quote'type": ['9999-12-31 23:59:59 +0800'],
        '中文指标': ['2026-10-03 08:00:00 +0800'],
    }
    ah._bulk_insert([dict(type=metric, start_date=stamp, end_date=stamp, value=1,
                         unit='count', source_name='synthetic')
                     for metric, stamps in by_type.items() for stamp in stamps], [])
    with ah._conn() as conn:
        expected = dict(conn.execute('SELECT type,MAX(substr(start_date,1,10)) FROM health_records GROUP BY type').fetchall())
    assert ah.get_latest_dates() == expected
    for metric in by_type:
        if metric:
            assert ah.get_latest_dates(metric) == {metric: expected[metric] or None}
