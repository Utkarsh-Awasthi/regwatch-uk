from regwatch.diff import diff_versions
from regwatch.faithfulness import score_summary
from regwatch.summarise import extractive, summarise_change
from regwatch.text import renumber


def P(*texts):
    return renumber([{"heading": "Threshold", "text": t, "kind": "body"} for t in texts])


REPORT = diff_versions(P("You must register if turnover is over £85,000."),
                       P("From 1 April 2027 you must register if turnover is over £90,000.")).to_dict()


def test_extractive_summary_is_fully_faithful():
    s = extractive(REPORT, "VAT")
    res = score_summary(s, REPORT)
    assert res.score == 1.0, [c.problems for c in res.claims]
    assert s["deadline"]["date"] == "2027-04-01"
    assert all(c["citations"] for c in s["claims"])


def test_wrong_number_is_caught():
    s = {"claims": [{"text": "The threshold changed from £85,000 to £95,000.", "citations": ["old:p1", "new:p1"]}]}
    assert score_summary(s, REPORT).score == 0


def test_swapped_direction_is_caught():
    s = {"claims": [{"text": "The threshold changed from £90,000 to £85,000.", "citations": ["old:p1", "new:p1"]}]}
    assert score_summary(s, REPORT).score == 0


def test_uncited_and_advice_claims_fail():
    s = {"claims": [{"text": "The threshold is now £90,000.", "citations": []},
                    {"text": "You should register now.", "citations": ["new:p1"]}]}
    res = score_summary(s, REPORT)
    assert res.score == 0
    assert any("advice" in p for c in res.claims for p in c.problems)


def test_quoting_the_source_you_must_is_allowed():
    s = {"claims": [{"text": 'It now reads: "you must register if turnover is over £90,000"', "citations": ["new:p1"]}]}
    assert score_summary(s, REPORT).score == 1.0


def test_llm_failing_gate_falls_back_to_extractive(monkeypatch):
    from regwatch import summarise
    monkeypatch.setattr(summarise, "llm", lambda *a, **k: {"headline": {"text": "Threshold up", "citations": ["new:p1"]},
                                                            "claims": [{"text": "It rose to £99,000.", "citations": ["new:p1"]}]})
    s, method, score, detail = summarise_change({"id": 1, "title": "VAT", "url": "", "report": REPORT}, use_llm=True)
    assert method == "extractive_fallback" and score == 1.0 and "rejected_llm" in detail


def test_llm_passing_gate_is_used(monkeypatch):
    from regwatch import summarise
    good = {"headline": {"text": "VAT threshold rises", "citations": ["new:p1"]},
            "claims": [{"text": "The registration threshold changed from £85,000 to £90,000.", "citations": ["old:p1", "new:p1"]}],
            "deadline": {"date": "2027-04-01", "citations": ["new:p1"]}}
    monkeypatch.setattr(summarise, "llm", lambda *a, **k: good)
    s, method, score, _ = summarise_change({"id": 1, "title": "VAT", "url": "", "report": REPORT}, use_llm=True)
    assert method == "llm" and score == 1.0
