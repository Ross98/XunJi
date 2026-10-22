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
