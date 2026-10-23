"""Tolerant display copies of training records; cached source data stays intact."""

from datetime import date


def is_training_date(value):
    try:
        return date.fromisoformat(value).isoformat() == value
    except (TypeError, ValueError):
        return False


def _sets_view(sets):
    if not isinstance(sets, list):
        return []
    result = []
    for group in sets:
        if not isinstance(group, dict):
            continue
        group = dict(group)
        if 'items' in group:
            items = group['items'] if isinstance(group['items'], list) else []
            group['items'] = [dict(item, set=dict(item['set'])) for item in items
                              if isinstance(item, dict) and isinstance(item.get('set'), dict)]
        result.append(group)
    return result


def training_records_view(records):
    result = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if 'datestr' in record and not is_training_date(record['datestr']):
            continue
        record = dict(record)
        if 'title' in record and not isinstance(record['title'], str):
            record['title'] = ''
        if 'movements' in record:
            movements = record['movements'] if isinstance(record['movements'], list) else []
            record['movements'] = []
            for movement in movements:
                if not isinstance(movement, dict):
                    continue
                movement = dict(movement)
                for field in ('name', 'type', 'exetype', 'difficulty'):
                    if field in movement and not isinstance(movement[field], str):
                        movement[field] = ''
                if 'sets' in movement:
                    movement['sets'] = _sets_view(movement['sets'])
                record['movements'].append(movement)
        result.append(record)
    return result
