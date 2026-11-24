"""Streamlit UI smoke tests (headless AppTest, temp DB, offline RAG)."""
from __future__ import annotations

import pytest

from tests.conftest import ROOT

APP = str(ROOT / "streamlit_app.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("PHARMA_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("PHARMA_RAG_BACKEND", "offline")
    import importlib

    import app.config as cfg
    importlib.reload(cfg)
    at = AppTest.from_file(APP, default_timeout=60)
    yield at
    monkeypatch.undo()
    importlib.reload(cfg)


def test_app_renders_without_errors(app):
    app.run()
    assert not app.exception
    assert [t.label for t in app.tabs][:5] == ["📝 New Intake", "📊 Dashboard", "✅ Closure Approvals",
                                               "🔎 Knowledge Assistant", "🧾 Audit Trail"]


def save(app):
    next(b for b in app.button if b.label == "Save report").click().run()


def test_empty_submit_shows_field_errors(app):
    app.run()
    save(app)
    assert not app.exception
    text = " ".join(m.value for m in app.markdown)
    assert "Product name" in text and "Batch/Lot number" in text and "Event onset date" in text
    assert any("not saved" in e.value for e in app.error)


def test_valid_submit_creates_reference(app):
    app.run()
    app.radio(key="intake_type").set_value("Product Quality Complaint").run()
    app.text_input(key="f_product").input("Zentrovax 50 mg tablets")
    app.text_input(key="f_batch").input("LOT-88392")
    app.selectbox(key="f_reporter").select("Healthcare Professional")
    app.text_area(key="f_description").input("Tablets show yellow discoloration in two blister strips.")
    save(app)
    assert not app.exception
    assert any("REF-" in s.value for s in app.success)


def test_invalid_operator_code_stops_app(app):
    app.run()
    app.text_input(key="operator_code").input("Jane Doe").run()
    assert any("Operator code" in e.value for e in app.error)


def _fill_pqc(app):
    app.radio(key="intake_type").set_value("Product Quality Complaint").run()
    app.text_input(key="f_product").input("Zentrovax 50 mg tablets")
    app.text_input(key="f_batch").input("LOT-88392")
    app.selectbox(key="f_reporter").select("Healthcare Professional")
    app.text_area(key="f_description").input("Tablets show yellow discoloration in two blister strips.")


def test_double_submit_shows_duplicate_warning(app):
    app.run()
    _fill_pqc(app)
    save(app)
    save(app)  # same form content submitted again
    assert not app.exception
    assert any("already saved" in w.value for w in app.warning)


def test_dashboard_table_filters_and_search(app):
    app.run()
    _fill_pqc(app)
    save(app)
    assert len(app.dataframe) >= 1 and "REF-" in app.dataframe[0].value["Reference"].iloc[0]
    app.multiselect[0].select("Closed").run()                  # status filter -> no rows
    assert any("No reports match" in i.value for i in app.info)
    app.multiselect[0].unselect("Closed").run()
    app.text_input(key="dash_search").input("LOT-00000").run()  # search with no match
    assert any("No reports match" in i.value for i in app.info)
    app.text_input(key="dash_search").input("lot-88392").run()  # case-insensitive batch search
    assert app.dataframe[0].value["Batch/Lot"].iloc[0] == "LOT-88392"
