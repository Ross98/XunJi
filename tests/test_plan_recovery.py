"""Recovery must use current evidence and preserve missing-data states."""
import asyncio
from datetime import date

import pytest
from jinja2 import Environment, FileSystemLoader
from starlette.requests import Request

from planner import generate_plan
from routes import plan


@pytest.fixture
def render_plan_context(monkeypatch, isolated_health_db, tmp_cache, tmp_path):
    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 22)

    context = {}

    class Template:
        def render(self, **kwargs):
            context.update(kwargs)
            return "ok"

    class Templates:
        def get_template(self, name):
            return Template()

    monkeypatch.setattr(plan, "date", Today)
    monkeypatch.setattr(plan, "get_cache", lambda: tmp_cache)
    monkeypatch.setattr(plan, "get_jinja_env", Templates)
    monkeypatch.setattr(plan, "PLAN_CONFIG_PATH", tmp_path / "plan.json")

    def render():
        asyncio.run(plan.plan_page(Request({"type": "http"})))
        return context

    return render


def add_record(ah, kind, day, value):
    with ah._conn() as conn:
        conn.execute(
            "INSERT INTO health_records (type,start_date,end_date,value) VALUES (?,?,?,?)",
            (f"HKQuantityTypeIdentifier{kind}", f"{day} 08:00:00 +0800",
             f"{day} 08:01:00 +0800", value),
        )


def add_sleep(ah, start, end, stage="AsleepCore"):
    with ah._conn() as conn:
        conn.execute("INSERT INTO health_sleep (type,start_date,end_date) VALUES (?,?,?)",
                     ("HKCategoryValueSleepAnalysis" + stage, start, end))


def test_old_health_samples_cannot_become_todays_readiness(
    render_plan_context, isolated_health_db
):
    ah = isolated_health_db
    add_record(ah, "HeartRateVariabilitySDNN", "2026-10-20", 40)
    add_record(ah, "RestingHeartRate", "2026-10-20", 60)
    add_sleep(ah, "2026-10-19 22:00:00 +0800", "2026-10-20 06:00:00 +0800")
    context = render_plan_context()
    assert "hrv" not in context["recovery"]
    assert "rhr" not in context["recovery"]
    assert "sleep_hours" not in context["recovery"]
    assert context["plan"]["readiness"]["score"] is None


def test_current_samples_and_last_night_sleep_still_contribute(
    render_plan_context, isolated_health_db
):
    ah = isolated_health_db
    add_record(ah, "HeartRateVariabilitySDNN", "2026-10-21", 40)
    add_record(ah, "HeartRateVariabilitySDNN", "2026-10-22", 20)
    add_record(ah, "RestingHeartRate", "2026-10-22", 60)
    add_sleep(ah, "2026-10-21 22:00:00 +0800", "2026-10-22 02:00:00 +0800")
    context = render_plan_context()
    assert context["recovery"]["hrv"] == 20
    assert context["recovery"]["hrv_avg"] == 30
    assert context["recovery"]["rhr"] == 60
    assert context["recovery"]["sleep_hours"] == 4
    assert context["plan"]["readiness"]["action"] == "rest"


def test_in_bed_only_is_not_zero_hours_of_sleep(render_plan_context, isolated_health_db):
    add_sleep(isolated_health_db, "2026-10-21 22:00:00 +0800",
              "2026-10-22 06:00:00 +0800", "InBed")
    assert "sleep_hours" not in render_plan_context()["recovery"]


@pytest.mark.parametrize("recovery", [None, {}, {"steps": 1000}, {"hrv": 40}])
def test_missing_recovery_never_claims_perfect_readiness(recovery):
    readiness = generate_plan([], recovery=recovery)["readiness"]
    assert readiness["score"] is None
    assert readiness["status"] == "数据不足"
    assert readiness["action"] == "train"


def test_unknown_recovery_renders_without_a_numeric_score():
    training = [{"datestr": "2026-10-21", "movements": [{"name": "卧推", "sets": [{}]}]}]
    result = generate_plan(training, reference_date=date(2026, 10, 22))
    env = Environment(loader=FileSystemLoader("templates"))
    html = env.get_template("plan.html").render(
        plan=result, preferences=result["preferences"], recovery={}, today="2026-10-22"
    )
    assert "数据不足" in html
    assert "None" not in html
    assert "/100" not in html


def test_midnight_does_not_drop_connected_stages_from_last_night(
    render_plan_context, isolated_health_db
):
    ah = isolated_health_db
    # Yesterday morning belongs to the previous night and must stay excluded.
    add_sleep(ah, "2026-10-21 01:00:00 +0800", "2026-10-21 07:00:00 +0800")
    add_sleep(ah, "2026-10-21 22:00:00 +0800", "2026-10-21 23:00:00 +0800")
    add_sleep(ah, "2026-10-21 23:00:00 +0800", "2026-10-22 02:00:00 +0800", "AsleepDeep")
    add_sleep(ah, "2026-10-22 02:00:00 +0800", "2026-10-22 06:00:00 +0800")
    assert render_plan_context()["recovery"]["sleep_hours"] == 8


def test_normal_rhr_is_real_evidence_even_without_other_metrics():
    readiness = generate_plan([], recovery={"rhr": 60, "rhr_avg": 60})["readiness"]
    assert readiness["score"] == 100
    assert readiness["reasons"] == ["静息心率未高于基线"]


def test_overlapping_steps_are_not_treated_as_confirmed_total(render_plan_context, isolated_health_db):
    with isolated_health_db._conn() as conn:
        for source, start, end in [('watch','08:00:00','09:00:00'), ('phone','08:30:00','09:30:00')]:
            conn.execute('INSERT INTO health_records (type,start_date,end_date,value,source_name) VALUES (?,?,?,?,?)',
                         ('HKQuantityTypeIdentifierStepCount', '2026-10-22 '+start+' +0800',
                          '2026-10-22 '+end+' +0800', 100, source))
    recovery = render_plan_context()['recovery']
    assert recovery['steps'] is None
    assert recovery['steps_overlap'] is True


@pytest.mark.parametrize('bad_metric,bad_key,good_metric,good_key', [
    ('HeartRateVariabilitySDNN','hrv','RestingHeartRate','rhr'),
    ('RestingHeartRate','rhr','HeartRateVariabilitySDNN','hrv'),
])
@pytest.mark.parametrize('bad', [None, 'bad', float('inf'), float('-inf')])
def test_invalid_metric_cannot_skip_other_current_recovery_inputs(
    render_plan_context, isolated_health_db, bad_metric, bad_key, good_metric, good_key, bad
):
    import json
    ah = isolated_health_db
    add_record(ah, bad_metric, '2026-10-22', bad)
    add_record(ah, good_metric, '2026-10-22', 60)
    add_record(ah, 'StepCount', '2026-10-22', 100)
    add_sleep(ah, '2026-10-21 22:00:00 +0800', '2026-10-22 04:00:00 +0800')
    before = ah.query_records()
    recovery = render_plan_context()['recovery']
    assert bad_key not in recovery
    assert bad_key+'_avg' not in recovery
    assert recovery[good_key] == 60
    assert recovery['steps'] == 100
    assert recovery['sleep_hours'] == 6
    json.dumps(recovery, allow_nan=False)
    assert ah.query_records() == before


def test_latest_missing_measurement_is_not_replaced_by_earlier_same_day_value(
    render_plan_context, isolated_health_db
):
    ah = isolated_health_db
    add_record(ah, 'HeartRateVariabilitySDNN', '2026-10-22', 40)
    with ah._conn() as conn:
        conn.execute('INSERT INTO health_records(type,start_date,end_date,value) VALUES (?,?,?,?)',
            ('HKQuantityTypeIdentifierHeartRateVariabilitySDNN','2026-10-22 20:00:00 +0800',
             '2026-10-22 20:01:00 +0800',None))
    assert 'hrv' not in render_plan_context()['recovery']


def test_recovery_baseline_uses_exact_thirty_days_and_preserves_zero(
    render_plan_context, isolated_health_db
):
    ah = isolated_health_db
    add_record(ah, 'HeartRateVariabilitySDNN', '2026-09-22', 999)
    add_record(ah, 'HeartRateVariabilitySDNN', '2026-09-23', 40)
    add_record(ah, 'HeartRateVariabilitySDNN', '2026-10-22', 0)
    recovery = render_plan_context()['recovery']
    assert recovery['hrv'] == 0
    assert recovery['hrv_avg'] == 20


def test_bad_step_interval_does_not_become_confirmed_recovery_input(render_plan_context, isolated_health_db):
    with isolated_health_db._conn() as conn:
        conn.execute('INSERT INTO health_records (type,start_date,end_date,value,source_name) VALUES (?,?,?,?,?)',
                     ('HKQuantityTypeIdentifierStepCount', '2026-10-22 08:00:00 +0800',
                      '2026-10-22 07:00:00 +0800', 100, 'synthetic'))
    recovery = render_plan_context()['recovery']
    assert recovery['steps'] is None
    assert recovery['steps_overlap'] is False


@pytest.mark.parametrize('kind,score,reason', [
    ('hrv_fraction', 70, 'HRV 低于30日基线'),
    ('hrv_zero', 70, 'HRV 低于30日基线'),
    ('rhr_fraction', 90, '静息心率略高'),
    ('sleep_fraction', 65, '睡眠明显不足'),
])
def test_route_scores_original_measurements_before_display_rounding(
    render_plan_context, isolated_health_db, kind, score, reason
):
    ah = isolated_health_db
    if kind == 'hrv_fraction':
        add_record(ah, 'HeartRateVariabilitySDNN', '2026-10-21', 48.4)
        add_record(ah, 'HeartRateVariabilitySDNN', '2026-10-22', 31.6)
    elif kind == 'hrv_zero':
        add_record(ah, 'HeartRateVariabilitySDNN', '2026-10-21', 40)
        add_record(ah, 'HeartRateVariabilitySDNN', '2026-10-22', 0)
    elif kind == 'rhr_fraction':
        add_record(ah, 'RestingHeartRate', '2026-10-21', 45)
        add_record(ah, 'RestingHeartRate', '2026-10-22', 60.6)
    else:
        add_sleep(ah, '2026-10-21 22:00:00 +0800', '2026-10-22 03:29:24 +0800')
    before = ah.query_records()
    context = render_plan_context()
    assert context['plan']['readiness']['score'] == score
    assert reason in context['plan']['readiness']['reasons']
    displayed = {
        'hrv_fraction': {'hrv': 32, 'hrv_avg': 40},
        'hrv_zero': {'hrv': 0, 'hrv_avg': 20},
        'rhr_fraction': {'rhr': 61, 'rhr_avg': 53},
        'sleep_fraction': {'sleep_hours': 5.5},
    }[kind]
    assert all(context['recovery'][key] == value for key, value in displayed.items())
    assert ah.query_records() == before
