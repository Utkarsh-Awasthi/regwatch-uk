from regwatch.diff import diff_versions
from regwatch.text import renumber


def P(*texts):
    return renumber([{"heading": "", "text": t, "kind": "body"} for t in texts])


def test_threshold_change_is_material_and_categorised():
    r = diff_versions(P("Register if turnover is over £85,000."), P("Register if turnover is over £90,000."))
    assert r.material and "threshold_or_rate" in r.categories
    pc = r.paragraphs[0]
    assert pc.op == "modified" and pc.facts_before == ["£85000"] and pc.facts_after == ["£90000"]


def test_typo_and_formatting_are_not_material():
    r = diff_versions(P("You must  register, within 30 days of recieving it."), P("You must register within 30 days of receiving it."))
    assert not r.material


def test_reordering_is_not_a_change():
    r = diff_versions(P("You must do A.", "You must do B."), P("You must do B.", "You must do A."))
    assert not r.material


def test_minor_update_caps_materiality():
    r = diff_versions(P("Fee is £100."), P("Fee is £200."), major_update=False)
    assert r.materiality <= 0.35 and not r.material


def test_new_document_is_material():
    r = diff_versions(None, P("Landlords must register by 1 May 2030."))
    assert r.kind == "new" and r.material and r.deadline == "2030-05-01"


def test_withdrawn():
    r = diff_versions(P("Old guidance."), P("Old guidance."), withdrawn=True)
    assert r.kind == "withdrawn" and r.material


def test_edited_paragraph_is_modified_not_removed_and_added():
    r = diff_versions(P("Intro.", "Landlords must protect deposits within 30 days.", "End."),
                      P("Intro.", "Landlords must protect deposits within 14 days.", "End."))
    assert [p.op for p in r.paragraphs] == ["modified"]
