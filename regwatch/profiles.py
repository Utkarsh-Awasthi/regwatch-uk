"""User profiles and the sector vocabulary used for relevance ranking.

Profiles are GDPR-minimal by design. We hold coarse tags ("landlord",
"employer"), a nation, an optional local authority, and a few banded
attributes. We never hold a name, a postcode, an address, or financial
figures. Bands like "2-5 properties" or "VAT registered: yes" are enough to
rank changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

NATIONS = {"england": "England", "wales": "Wales", "scotland": "Scotland", "northern_ireland": "Northern Ireland"}

# Each sector: weighted query terms (matched against changed text and title),
# GOV.UK taxons/organisations that strongly indicate relevance, and
# legislation subject headings.
SECTORS: dict[str, dict] = {
    "landlord": {
        "label": "Landlord / letting",
        "terms": {"landlord": 3, "landlords": 3, "tenant": 2.5, "tenants": 2.5, "tenancy": 3, "tenancies": 3,
                  "rent": 2, "rented": 2, "renting": 2, "letting": 2.5, "lettings": 2.5, "hmo": 3,
                  "multiple occupation": 3, "deposit": 2, "eviction": 3, "possession": 2, "section 21 notice": 3,
                  "section 8 notice": 3, "epc": 2.5, "energy performance": 2, "gas safety": 3, "smoke alarm": 2.5,
                  "carbon monoxide": 2.5, "licensing": 1.5, "selective licensing": 3, "decent homes": 2.5,
                  "right to rent": 3, "renters": 3, "dwelling": 1.5, "housing": 1, "letting agent": 2.5,
                  "private rented": 3, "assured": 1.5, "property": 0.7, "properties": 0.7,
                  "empty dwelling": 3, "empty homes": 3, "dwelling management order": 3, "council tax premium": 2.5,
                  "leasehold": 2, "leaseholder": 2, "cladding": 2.5, "building safety": 2, "awaab's law": 3,
                  "damp and mould": 3, "rent repayment order": 3, "banning order": 3, "property portal": 3,
                  "landlord ombudsman": 3, "housing health and safety rating": 3},
        "topics": {"Rented housing sector", "Private renting", "Landlords", "Renting and housing standards",
                   "Housing health and safety"},
        "subjects": {"LANDLORD AND TENANT", "HOUSING", "RENT"},
    },
    "food_business": {
        "label": "Food business / café / restaurant",
        "terms": {"food": 1.5, "allergen": 3, "allergens": 3, "allergy": 2.5, "labelling": 2.5, "label": 1.5,
                  "hygiene": 2.5, "food hygiene rating": 3, "takeaway": 2, "restaurant": 2, "cafe": 2,
                  "catering": 2.5, "prepacked": 3, "pre-packed": 3, "ppds": 3, "calorie": 2.5, "nutrition": 2,
                  "food business": 3, "food standards": 2.5, "haccp": 3, "drink": 1, "alcohol": 1.5,
                  "premises licence": 2.5, "high fat": 2, "hfss": 3},
        "topics": {"Food and farming", "Food Standards Agency", "Food labelling and safety", "Food and drink",
                   "Hospitality"},
        "subjects": {"FOOD", "LICENSING (LIQUOR)"},
    },
    "employer": {
        "label": "Employer (has staff)",
        "terms": {"employer": 3, "employers": 3, "employee": 2.5, "employees": 2.5, "worker": 2, "workers": 2,
                  "staff": 1.5, "wage": 2.5, "minimum wage": 3, "living wage": 3, "national minimum wage": 3,
                  "payroll": 3, "paye": 3, "national insurance": 2.5, "statutory sick pay": 3, "ssp": 2.5,
                  "maternity": 2, "paternity": 2, "holiday pay": 3, "pension": 2, "auto-enrolment": 3,
                  "right to work": 3, "dismissal": 2.5, "unfair dismissal": 3, "flexible working": 3,
                  "employment": 2, "tribunal": 1.5, "zero hours": 3, "redundancy": 2.5, "apprentice": 2},
        "topics": {"Employing people", "Pay and contracts", "Workplace pensions", "Payroll", "Employment rights",
                   "Recruiting and hiring", "Health and safety at work"},
        "subjects": {"EMPLOYMENT", "MINIMUM WAGE", "PENSIONS, ENGLAND AND WALES", "OCCUPATIONAL PENSIONS"},
    },
    "sole_trader": {
        "label": "Sole trader / self-employed",
        "terms": {"self-employed": 3, "self employed": 3, "sole trader": 3, "self assessment": 3,
                  "making tax digital": 3, "mtd": 3, "income tax": 2.5, "tax return": 2.5, "trading allowance": 3,
                  "class 2": 2.5, "class 4": 2.5, "allowable expenses": 2.5, "turnover": 2, "vat": 2,
                  "vat registration": 3, "threshold": 1.5, "hmrc": 1.5, "personal allowance": 2},
        "topics": {"Self Assessment", "Business tax", "VAT", "Income Tax", "Making Tax Digital",
                   "Self-employment"},
        "subjects": {"INCOME TAX", "VALUE ADDED TAX", "TAXES"},
    },
    "limited_company": {
        "label": "Limited company director",
        "terms": {"company": 1.5, "companies": 1.5, "director": 2, "directors": 2, "company director": 3, "companies house": 3,
                  "confirmation statement": 3, "identity verification": 3, "corporation tax": 3,
                  "annual accounts": 3, "accounts": 1.5, "dividend": 2.5, "vat": 2, "vat registration": 3,
                  "persons with significant control": 3, "psc": 2.5, "micro-entity": 3},
        "topics": {"Companies House", "Corporation Tax", "Running a limited company", "Business tax", "VAT",
                   "Company registration and filing"},
        "subjects": {"COMPANIES", "CORPORATION TAX", "VALUE ADDED TAX"},
    },
    "charity": {
        "label": "Charity / community group",
        "terms": {"charity": 3, "charities": 3, "trustee": 3, "trustees": 3, "charitable": 2.5, "gift aid": 3,
                  "fundraising": 2.5, "charity commission": 3, "annual return": 2.5, "cio": 2.5,
                  "volunteer": 2, "volunteers": 2, "safeguarding": 2, "donation": 2, "donations": 2},
        "topics": {"Charities and social enterprises", "Charity Commission", "Running a charity", "Gift Aid",
                   "Charities"},
        "subjects": {"CHARITIES"},
    },
    "retailer": {
        "label": "Shop / retailer",
        "terms": {"retail": 2, "retailer": 3, "shop": 2, "shops": 2, "consumer": 2, "consumer rights": 3,
                  "pricing": 2, "price marking": 3, "sunday trading": 3, "packaging": 2.5,
                  "extended producer responsibility": 3, "deposit return": 3, "age restricted": 3,
                  "vape": 2.5, "vapes": 2.5, "tobacco": 2.5, "plastic": 2, "product safety": 3, "weee": 3},
        "topics": {"Consumer protection", "Trading Standards", "Retail", "Packaging and waste",
                   "Office for Product Safety and Standards", "Product safety"},
        "subjects": {"CONSUMER PROTECTION", "ENVIRONMENTAL PROTECTION", "TRADE DESCRIPTIONS"},
    },
}

# Attributes we allow on a profile. Anything else is dropped on save.
ALLOWED_ATTRIBUTES = {
    "properties_band": ["1", "2-5", "6-20", "21+"],
    "employees_band": ["0", "1-9", "10-49", "50-249"],
    "vat_registered": ["yes", "no", "unsure"],
    "hmo": ["yes", "no"],
    "sells_prepacked_food": ["yes", "no"],
}


@dataclass
class Profile:
    sectors: list[str]
    nation: str
    local_authority: str | None = None
    attributes: dict = field(default_factory=dict)
    id: int | None = None
    label: str | None = None

    @classmethod
    def from_row(cls, row) -> "Profile":
        import json
        return cls(sectors=json.loads(row["sectors"]), nation=row["nation"], local_authority=row["local_authority"],
                   attributes=json.loads(row["attributes"] or "{}"), id=row["id"], label=row["label"])

    def describe(self) -> str:
        parts = [", ".join(SECTORS[s]["label"] for s in self.sectors if s in SECTORS)]
        place = self.local_authority or NATIONS.get(self.nation, self.nation)
        parts.append(place)
        for k, v in self.attributes.items():
            parts.append(f"{k.replace('_band', '').replace('_', ' ')}: {v}")
        return " · ".join(p for p in parts if p)


def validate(sectors: list[str], nation: str, local_authority: str | None, attributes: dict) -> tuple[list[str], str, str | None, dict]:
    sectors = [s for s in sectors if s in SECTORS]
    if not sectors:
        raise ValueError("Choose at least one type of business.")
    if nation not in NATIONS:
        raise ValueError("Choose a nation.")
    la = (local_authority or "").strip()[:80] or None
    attrs = {k: v for k, v in (attributes or {}).items() if k in ALLOWED_ATTRIBUTES and v in ALLOWED_ATTRIBUTES[k]}
    return sectors, nation, la, attrs
