from analysis import training_type_distribution


def movement(name, minutes=None, calories=None, kind='cardio'):
    entry = {}
    if minutes is not None:
        entry['time'] = minutes * 60
    if calories is not None:
        entry['calories'] = calories
    return {'name': name, 'exetype': kind, 'sets': [entry]}


def test_mixed_session_uses_each_types_actual_duration_and_calories():
    trains = [{'start': 0, 'end': 60 * 60000, 'movements': [
        movement('跑步', 10, 100), movement('骑行', 30, 300),
        movement('卧推', None, None, 'weight'),
    ]}]
    rows = {row['name']: row for row in training_type_distribution(trains, cardio_only=True)}
    assert rows['跑步']['duration_min'] == 10
    assert rows['跑步']['calories'] == 100
    assert rows['骑行']['duration_min'] == 30
    assert rows['骑行']['calories'] == 300


def test_unmeasured_type_does_not_borrow_another_types_calories_or_time():
    trains = [{'start': 0, 'end': 60 * 60000, 'movements': [
        movement('跑步', 10, 100), movement('骑行'),
    ]}]
    rows = {row['name']: row for row in training_type_distribution(trains)}
    assert rows['骑行']['duration_min'] is None
    assert rows['骑行']['known_duration_min'] == 0
    assert 'calories' not in rows['骑行']


def test_partial_history_keeps_total_unknown_and_reports_only_known_minutes():
    trains = [
        {'movements': [movement('跑步', 20, 100)]},
        {'movements': [movement('跑步')]},
    ]
    row, = training_type_distribution(trains)
    assert row['count'] == 2
    assert row['duration_min'] is None
    assert row['known_duration_min'] == 20


def test_duplicate_type_blocks_count_once_and_sum_their_own_metrics():
    trains = [{'start': 0, 'end': 60*60000, 'movements': [
        movement('跑步', 10, 100), movement('跑步', 30, 300),
    ]}]
    row, = training_type_distribution(trains)
    assert row['count'] == 1
    assert row['duration_min'] == 40
    assert row['calories'] == 400


def test_sole_type_can_use_observed_session_interval_when_set_times_are_absent():
    row, = training_type_distribution([{'start': 0, 'end': 45*60000,
                                         'movements': [movement('跑步')]}])
    assert row['duration_min'] == 45


def test_same_label_with_different_exercise_types_cannot_supply_cardio_duration():
    train = {'start': 0, 'end': 60*60000, 'movements': [
        movement('划船', None, None, 'weight'), movement('划船'),
    ]}
    row, = training_type_distribution([train], cardio_only=True)
    assert row['duration_min'] is None
