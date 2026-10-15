from regwatch import policy
from regwatch.profiles import Profile, validate
from regwatch.rank import rank, score

import pytest


def ch(title, text, nations=(), topics=(), mat=0.9, cats=("threshold_or_rate",)):
    return {"id": 1, "title": title, "nations": list(nations), "topics": list(topics), "materiality": mat,
            "categories": list(cats), "report": {"paragraphs": [{"new_text": text}]}}


LANDLORD = Profile(["landlord"], "england", "Plymouth")


def test_other_nation_is_excluded():
    r = score(ch("Short-term lets", "Hosts must hold a licence.", ["scotland"]), LANDLORD)
    assert r.band == "excluded" and "Scotland" in r.reasons[0]


def test_relevant_landlord_change_is_likely_with_reasons():
    r = score(ch("Deposit rules for landlords", "Landlords must protect a tenant's deposit within 14 days.", ["england"],
                 ["Private renting"]), LANDLORD)
    assert r.band == "likely" and any("Mentions" in x for x in r.reasons)


def test_own_council_boosted_other_council_excluded():
    own = score(ch("Selective licensing: Plymouth City Council", "Landlords of private rented homes need a licence."), LANDLORD)
    other = score(ch("Selective licensing: Leeds City Council", "Landlords of private rented homes need a licence."), LANDLORD)
    assert own.score > other.score and other.band == "excluded"


def test_immaterial_change_never_ranked():
    assert score(ch("Landlords guide", "Landlords tenancy deposit", mat=0.2), LANDLORD).band == "excluded"


def test_feedback_damps_topic():
    c = ch("Deposit rules for landlords", "Landlords must protect deposits.", ["england"], ["Private renting"])
    assert score(c, LANDLORD, {"Private renting": -3}).score < score(c, LANDLORD).score


def test_rank_orders_by_band():
    items = [ch("Landlord licensing", "Landlords licensing tenancy", ["england"], ["Private renting"]),
             ch("Fishing quota", "Vessels report catches")]
    assert len(rank(items, LANDLORD)) == 1


def test_profile_validation_drops_unknown_fields():
    s, n, la, attrs = validate(["landlord", "astronaut"], "england", " Plymouth ", {"hmo": "yes", "income": "£1m"})
    assert s == ["landlord"] and la == "Plymouth" and attrs == {"hmo": "yes"}
    with pytest.raises(ValueError):
        validate([], "england", None, {})


def test_policy_refuses_ambiguous_and_labels_proposals():
    amb = {"title": "x", "report": {"paragraphs": [{"new_text": "Take reasonable steps where appropriate."}]}}
    assert policy.assess(amb, "possible")["refuse_applicability"]
    cons = {"title": "Consultation on licensing", "report": {"paragraphs": []}}
    assert policy.assess(cons, "likely")["proposal"]
    assert policy.advice_violations("We recommend you register") == ["We recommend"]


def test_bill_is_a_proposal_but_electricity_bill_is_not():
    assert policy.assess({"title": "Guide to the Building Safety Bill", "report": {"paragraphs": []}}, "likely")["proposal"]
    assert not policy.assess({"title": "Help with your electricity bill", "report": {"paragraphs": []}}, "likely")["proposal"]
