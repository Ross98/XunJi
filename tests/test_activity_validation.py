"""Unusable cumulative intervals cannot silently turn into confirmed daily totals."""
from copy import deepcopy

import pytest

from activity_stats import daily_activity_totals, confirmed_activity_average
from test_activity_stats import row

DAY = '2026-10-22'


@pytest.mark.parametrize('bad', [
    {'end_date': 'not-a-time'}, {'end_date': None},
    {'start_date': DAY, 'end_date': DAY},
    {'end_date': DAY + ' 07:59:59 +0800'},
    {'start_date': DAY + ' invalid-time'},
    {'start_date': DAY + 'T08:00:00Z', 'end_date': DAY + 'T09:00:00+02:00'},
    {'start_date': DAY + 'T08:00:00.123456789Z', 'end_date': DAY + 'T08:00:00.123456788Z'},
])
def test_bad_interval_makes_the_day_unknown_instead_of_a_partial_sum(bad):
    samples = [row('watch', '08:00:00', '09:00:00', 100),
               {**row('watch', '10:00:00', '11:00:00', 80), **bad}]
    original = deepcopy(samples)
    assert daily_activity_totals(samples)[DAY]['value'] is None
    assert confirmed_activity_average(samples, DAY, DAY) == (None, 0)
    assert samples == original


def test_daily_sum_uses_accurate_finite_accumulation():
    samples = [row('watch', f'{h}:00:00', f'{h}:30:00', value)
               for h, value in [('08', 1e16), ('09', 1), ('10', 1)]]
    assert daily_activity_totals(samples)[DAY]['value'] == 10000000000000002


def test_unrepresentable_daily_sum_is_unknown():
    samples = [row('watch', '08:00:00', '09:00:00', 1e308),
               row('watch', '10:00:00', '11:00:00', 1e308)]
    assert daily_activity_totals(samples)[DAY]['value'] is None
    assert confirmed_activity_average(samples, DAY, DAY) == (None, 0)


def test_finite_daily_values_do_not_overflow_their_average():
    samples = []
    for day in [DAY, '2026-10-21']:
        sample = row('watch', '08:00:00', '09:00:00', 1e308)
        for key in ('start_date', 'end_date'): sample[key] = sample[key].replace(DAY, day)
        samples.append(sample)
    assert confirmed_activity_average(samples, '2026-10-21', DAY) == (round(1e308), 2)


def test_invalid_calendar_days_and_nonrecords_cannot_create_observed_days():
    bad_day = row('watch', '08:00:00', '09:00:00', 100)
    bad_day['start_date'] = bad_day['start_date'].replace(DAY, '2026-02-30')
    samples = [None, [], bad_day]
    assert daily_activity_totals(samples) == {}


def test_valid_legacy_local_clock_intervals_can_still_be_compared():
    samples = [row('watch', '08:00:00', '09:00:00', 100), row('phone', '09:00:00', '10:00:00', 80)]
    for sample in samples:
        for key in ('start_date', 'end_date'): sample[key] = sample[key].replace(' +0800', '')
    assert daily_activity_totals(samples)[DAY] == {'value':180,'overlap':False}


def test_mixed_naive_and_aware_intervals_do_not_guess_a_timezone():
    first = row('watch', '08:00:00', '09:00:00', 100)
    second = row('phone', '09:00:00', '10:00:00', 80)
    for key in ('start_date', 'end_date'): second[key] = second[key].replace(' +0800', '')
    assert daily_activity_totals([first, second])[DAY]['value'] is None


def test_valid_zero_duration_measurement_is_not_missing():
    assert daily_activity_totals([row('watch','08:00:00','08:00:00',0)])[DAY]['value'] == 0


@pytest.mark.parametrize('second_start,overlap', [('123456783', True), ('123456785', False)])
def test_overlap_comparison_retains_submicrosecond_precision(second_start, overlap):
    first = {'source_name':'watch','start_date':DAY+'T08:00:00.123456781Z',
             'end_date':DAY+'T08:00:00.123456785Z','value':100}
    second = {'source_name':'phone','start_date':DAY+'T08:00:00.'+second_start+'Z',
              'end_date':DAY+'T08:00:00.123456787Z','value':80}
    result = daily_activity_totals([first, second])[DAY]
    assert result['overlap'] is overlap
    assert result['value'] == (None if overlap else 180)
