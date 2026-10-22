import asyncio
import json

import pytest

from planner import DEFAULT_PLAN_PREFERENCES, generate_plan
from routes import plan


@pytest.mark.parametrize('saved', [[], None, 'broken', 3, {'days_per_week': 'bad', 'equipment': []}])
def test_malformed_saved_preferences_fall_back_to_safe_defaults(tmp_path, monkeypatch, saved):
    config = tmp_path / 'plan.json'
    config.write_text(json.dumps(saved))
    monkeypatch.setattr(plan, 'PLAN_CONFIG_PATH', config)
    assert plan._load_plan_preferences() == DEFAULT_PLAN_PREFERENCES


@pytest.mark.parametrize('payload', [
    {'days_per_week': float('inf')}, {'session_minutes': float('nan')},
    {'days_per_week': True}, {'equipment': []}, {'injuries': {}}, {'goal': None},
])
def test_invalid_settings_are_rejected_without_writing(tmp_path, monkeypatch, payload):
    config = tmp_path / 'plan.json'
    monkeypatch.setattr(plan, 'PLAN_CONFIG_PATH', config)
    result = asyncio.run(plan.update_plan_settings(payload))
    assert result['status'] == 'error'
    assert not config.exists()


def test_valid_settings_still_clamp_numbers_and_ignore_unknown_keys(tmp_path, monkeypatch):
    config = tmp_path / 'plan.json'
    monkeypatch.setattr(plan, 'PLAN_CONFIG_PATH', config)
    result = asyncio.run(plan.update_plan_settings({
        'days_per_week': 9, 'session_minutes': '10', 'equipment': '哑铃', 'unknown': 'ignored',
    }))
    assert result['status'] == 'ok'
    assert result['preferences']['days_per_week'] == 7
    assert result['preferences']['session_minutes'] == 15
    assert 'unknown' not in json.loads(config.read_text())


def test_plan_engine_handles_invalid_stored_numeric_values():
    result = generate_plan([], preferences={'days_per_week': float('inf')})
    assert result['preferences']['days_per_week'] == DEFAULT_PLAN_PREFERENCES['days_per_week']


def test_save_failure_keeps_previous_preferences(tmp_path, monkeypatch):
    config = tmp_path / 'plan.json'
    original = json.dumps(DEFAULT_PLAN_PREFERENCES)
    config.write_text(original)
    monkeypatch.setattr(plan, 'PLAN_CONFIG_PATH', config)

    def fail_replace(*args):
        raise OSError('simulated disk error')

    import os
    monkeypatch.setattr(os, 'replace', fail_replace)
    result = asyncio.run(plan.update_plan_settings({'days_per_week': 2}))
    assert result['status'] == 'error'
    assert config.read_text() == original
    assert list(tmp_path.iterdir()) == [config]
