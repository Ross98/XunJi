from activity_stats import daily_activity_totals


def row(source, start, end, value):
    return {'source_name': source, 'start_date': '2026-10-22 '+start+' +0800',
            'end_date': '2026-10-22 '+end+' +0800', 'value': value}


def test_overlapping_sources_do_not_create_a_confirmed_total():
    result = daily_activity_totals([row('watch','08:00:00','09:00:00',100),
                                    row('phone','08:30:00','09:30:00',80)])['2026-10-22']
    assert result['value'] is None
    assert result['overlap'] is True


def test_nonoverlapping_sources_and_touching_boundaries_can_be_summed():
    result = daily_activity_totals([row('watch','08:00:00','09:00:00',100),
                                    row('phone','09:00:00','09:30:00',80)])['2026-10-22']
    assert result['value'] == 180
    assert result['overlap'] is False


def test_source_overlap_comparison_honors_offsets():
    first = row('watch','08:00:00','09:00:00',100)
    second = {'source_name': 'phone', 'start_date': '2026-10-22T00:30:00Z',
              'end_date': '2026-10-22T01:30:00Z', 'value': 80}
    assert daily_activity_totals([first, second])['2026-10-22']['overlap'] is True


def test_observed_zero_is_preserved_and_invalid_values_are_not_confirmed():
    result = daily_activity_totals([row('watch','08:00:00','09:00:00',0)])['2026-10-22']
    assert result['value'] == 0
    for value in [float('inf'), 'bad', True, -1]:
        result = daily_activity_totals([row('watch','08:00:00','09:00:00',value)])['2026-10-22']
        assert result['value'] is None


def test_daily_average_uses_confirmed_days_including_real_zero():
    from activity_stats import confirmed_activity_average
    samples = [row('watch','08:00:00','09:00:00',100), row('phone','08:30:00','09:30:00',80)]
    for day, value in [('2026-10-21', 10), ('2026-10-20', 0)]:
        sample = row('watch','08:00:00','09:00:00',value)
        for key in ('start_date', 'end_date'):
            sample[key] = sample[key].replace('2026-10-22', day)
        samples.append(sample)
    assert confirmed_activity_average(samples, '2026-10-20', '2026-10-22') == (5, 2)
    assert confirmed_activity_average(samples, '2026-10-22', '2026-10-22') == (None, 0)
