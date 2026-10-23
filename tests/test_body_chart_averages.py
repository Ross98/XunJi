"""Run the shipped chart arithmetic in JavaScript, with no browser or network."""

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

NODE = shutil.which('node')


def average(rows):
    if NODE is None:
        pytest.skip('Node.js is needed for the shipped JavaScript arithmetic')
    template = (Path(__file__).parents[1] / 'templates/body.html').read_text()
    code = re.search(r'function avgByDate\(arr\) \{.*?\n\}', template, re.S).group(0)
    script = code + '\nprocess.stdout.write(JSON.stringify(avgByDate(JSON.parse(process.argv[1]))));'
    result = subprocess.run([NODE, '-e', script, json.dumps(rows)], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('values,expected', [
    (['70', '72'], 71), (['70.2', '71.4'], 70.8), ([70, '72'], 71),
    ([0, '0'], 0), ([1e308, 1e308], 1e308), ([-1e308, 1e308], 0),
])
def test_same_day_values_are_numeric_averages(values, expected):
    result = average([{'date': '2026-10-23', 'value': value} for value in values])
    assert len(result) == 1
    assert result[0]['date'] == '2026-10-23'
    assert result[0]['value'] == pytest.approx(expected)


@pytest.mark.parametrize('invalid', [None, True, False, '', ' ', 'bad', 'NaN', 'Infinity', [], {}])
def test_chart_invalid_values_do_not_become_zero_or_pollute_daily_average(invalid):
    rows = [{'date': '2026-10-23', 'value': 70}, {'date': '2026-10-23', 'value': invalid}]
    assert average(rows) == [{'date': '2026-10-23', 'value': 70}]


def test_daily_chart_points_keep_date_order_and_zero_values():
    rows = [{'date': '2026-10-23', 'value': '72'},
            {'date': '2026-10-21', 'value': '0'},
            {'date': '2026-10-23', 'value': '70'}]
    assert average(rows) == [{'date': '2026-10-21', 'value': 0}, {'date': '2026-10-23', 'value': 71}]
