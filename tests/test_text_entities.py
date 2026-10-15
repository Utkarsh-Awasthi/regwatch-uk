from datetime import date

from regwatch import entities
from regwatch.text import comparable, html_to_paragraphs


def test_html_to_paragraphs_tracks_headings_and_lists():
    html = "<h2>Deposits</h2><p>You must protect it.</p><ul><li>within 30 days</li><li>in a scheme</li></ul><script>x()</script>"
    ps = html_to_paragraphs(html)
    assert [p["text"] for p in ps] == ["Deposits", "You must protect it.", "within 30 days", "in a scheme"]
    assert ps[0]["kind"] == "heading" and ps[2]["heading"] == "Deposits"
    assert [p["id"] for p in ps] == ["p1", "p2", "p3", "p4"]


def test_comparable_ignores_case_punctuation_and_links():
    assert comparable("Read THIS, at https://a.b/c.") == comparable("read this at")


def test_facts_extracts_money_percent_dates_durations():
    f = entities.facts("From 1 April 2027 the fee is £1,100 (up 5%), payable within 28 days.")
    assert f["money"] == [1100.0] and f["percent"] == [5.0]
    assert f["date"] == [date(2027, 4, 1)] and f["duration"] == [(28.0, "day")]


def test_month_may_is_not_a_permission():
    assert "permission" not in entities.deontic("Rules change in May 2027.")
    assert entities.deontic("You may apply.")["permission"] == 1


def test_deadline_needs_a_cue_and_a_future_date():
    assert entities.deadline("This applies from 1 March 2027.", date(2026, 1, 1)) == date(2027, 3, 1)
    assert entities.deadline("Published 1 March 2027.", date(2026, 1, 1)) is None
    assert entities.deadline("This applied from 1 March 2020.", date(2026, 1, 1)) is None
