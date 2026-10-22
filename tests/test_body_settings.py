import asyncio
import json

import pytest

from routes import body


@pytest.mark.parametrize('raw', ['{broken', '[]', 'null', '{"height_cm": []}',
                                '{"height_cm": 0}', '{"height_cm": "NaN"}'])
def test_bad_body_config_falls_back_without_overwriting_file(tmp_path, monkeypatch, raw):
    path = tmp_path / 'body.json'
    path.write_text(raw)
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(path))
    assert body._load_body_config()['height_cm'] == 170
    assert path.read_text() == raw


def test_valid_body_height_and_other_preferences_are_preserved(tmp_path, monkeypatch):
    path = tmp_path / 'body.json'
    path.write_text(json.dumps({'height_cm': 182.5, 'other': 'keep'}))
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(path))
    assert body._load_body_config()['height_cm'] == 182.5
    assert asyncio.run(body.update_body_settings({'height_cm': 181}))['status'] == 'ok'
    assert json.loads(path.read_text()) == {'height_cm': 181, 'other': 'keep'}


def test_failed_body_save_preserves_previous_configuration(tmp_path, monkeypatch):
    import os
    path = tmp_path / 'body.json'
    original = '{"height_cm": 182}'
    path.write_text(original)
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(path))

    def fail_replace(*args):
        raise OSError('simulated disk error')

    monkeypatch.setattr(os, 'replace', fail_replace)
    result = asyncio.run(body.update_body_settings({'height_cm': 181}))
    assert result['status'] == 'error'
    assert path.read_text() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('height', [None, [], True, 99, 251, float('nan'), float('inf')])
def test_invalid_body_settings_are_rejected(tmp_path, monkeypatch, height):
    path = tmp_path / 'body.json'
    monkeypatch.setattr(body, 'BODY_CONFIG_PATH', str(path))
    assert asyncio.run(body.update_body_settings({'height_cm': height}))['status'] == 'error'
    assert not path.exists()
