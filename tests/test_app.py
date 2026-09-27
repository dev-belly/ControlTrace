"""Exercise the review flow and filter reruns through Streamlit's test harness."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from controltrace.store import get_table_rows

APP_PATH = Path(__file__).parents[1] / "src" / "controltrace" / "app.py"


def test_review_form_only_offers_conclusions_valid_for_status(tmp_path, monkeypatch):
    db_path = tmp_path / "case.duckdb"
    monkeypatch.setenv("CONTROLTRACE_DB", str(db_path))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)
    assert not app.exception
    assert app.selectbox[0].value == "pending"
    assert app.selectbox[1].value is None
    assert app.selectbox[1].options == ["No conclusion"]
    app.text_input[0].set_value("Demo Analyst").run()
    app.text_area[0].set_value("Assigned for review").run()
    app.button[0].click().run()
    assert not app.exception
    reviews = get_table_rows(db_path, "reviews")
    assert len(reviews) == 1
    assert reviews[0]["status"] == "pending"
    assert reviews[0]["conclusion"] is None

    app.selectbox[0].set_value("in_review").run()
    assert not app.exception
    assert app.selectbox[1].options == ["No conclusion", "needs more evidence"]
    app.selectbox[1].set_value("needs_more_evidence").run()
    app.text_area[0].set_value("Request the original access approval").run()
    app.button[0].click().run()
    assert not app.exception
    reviews = get_table_rows(db_path, "reviews")
    assert len(reviews) == 2
    assert reviews[-1]["status"] == "in_review"
    assert reviews[-1]["conclusion"] == "needs_more_evidence"

    app.selectbox[0].set_value("closed").run()
    assert not app.exception
    assert app.selectbox[1].options == ["confirmed exception", "false positive"]
    app.selectbox[1].set_value("false_positive").run()
    app.text_area[0].set_value("Original approval proves the grant was authorized").run()
    app.button[0].click().run()
    assert not app.exception
    reviews = get_table_rows(db_path, "reviews")
    assert len(reviews) == 3
    assert reviews[-1]["status"] == "closed"
    assert reviews[-1]["conclusion"] == "false_positive"


def test_filter_changes_reset_table_selection_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROLTRACE_DB", str(tmp_path / "case.duckdb"))
    app = AppTest.from_file(str(APP_PATH)).run(timeout=30)
    assert not app.exception
    original_key = app.dataframe[1].key
    system = app.multiselect[0].options[0]
    app.multiselect[0].set_value([system]).run()
    assert not app.exception
    assert app.dataframe[1].key != original_key

    app.multiselect[0].set_value([]).run()
    assert not app.exception
    assert any("No observations match" in item.value for item in app.info)
