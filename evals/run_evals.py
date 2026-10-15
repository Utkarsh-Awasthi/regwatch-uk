"""Offline evaluation suite. Writes evals/report.md and evals/report.json.

1. Change detection: precision, recall and F1 of the "material" decision,
   plus category accuracy, on hand-labelled before/after pairs.
2. Relevance ranking: precision and recall of "show this to this profile"
   on hand-labelled (profile, change) pairs, overall and for the "likely" band.
3. Faithfulness checker: (a) false-alarm rate on faithful summaries, and
   (b) detection rate on summaries with injected hallucinations: wrong
   number, shifted date, advice, missing or bogus citation, invented claim,
   altered quotation.
4. Policy: advice-language detector and refusal triggers.
5. (``--llm``) Claude summaries: share passing the faithfulness gate, plus
   an LLM-judge entailment rate.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regwatch import policy  # noqa: E402
from regwatch.demo import DOCS  # noqa: E402
from regwatch.diff import diff_versions  # noqa: E402
from regwatch.faithfulness import score_summary  # noqa: E402
from regwatch.profiles import Profile  # noqa: E402
from regwatch.rank import score as rank_score  # noqa: E402
from regwatch.summarise import extractive  # noqa: E402
from regwatch.text import renumber  # noqa: E402

FIX = Path(__file__).with_name("fixtures")


def paras(texts):
    return renumber([{"heading": "", "text": t, "kind": "body"} for t in texts])


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 3), round(r, 3), round(f, 3)


def eval_change_detection():
    rows = [json.loads(line) for line in (FIX / "changes.jsonl").read_text().splitlines() if line.strip()]
    tp = fp = fn = tn = 0
    cat_ok = cat_n = 0
    failures = []
    for r in rows:
        rep = diff_versions(paras(r["old"]), paras(r["new"]), major_update=True)
        pred = rep.material
        if pred and r["material"]:
            tp += 1
        elif pred and not r["material"]:
            fp += 1
            failures.append(f"false alarm `{r['id']}` (score {rep.materiality}, {rep.categories})")
        elif not pred and r["material"]:
            fn += 1
            failures.append(f"missed `{r['id']}` (score {rep.materiality}, {rep.categories})")
        else:
            tn += 1
        if r["material"] and r["expect"]:
            cat_n += 1
            if set(r["expect"]) <= set(rep.categories):
                cat_ok += 1
            else:
                failures.append(f"category `{r['id']}`: expected {r['expect']}, got {rep.categories}")
    p, rc, f = prf(tp, fp, fn)
    return {"n": len(rows), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": p, "recall": rc, "f1": f,
            "category_accuracy": round(cat_ok / cat_n, 3) if cat_n else None, "failures": failures}


def eval_relevance():
    data = json.loads((FIX / "relevance.json").read_text())
    stats = {"any": [0, 0, 0], "likely": [0, 0, 0]}
    failures = []
    for pid, cid, label in data["labels"]:
        pr = data["profiles"][pid]
        prof = Profile(pr["sectors"], pr["nation"], pr.get("local_authority"), pr.get("attributes", {}))
        r = rank_score(data["changes"][cid], prof)
        for key, pred in (("any", r.band != "excluded"), ("likely", r.band == "likely")):
            s = stats[key]
            if pred and label:
                s[0] += 1
            elif pred and not label:
                s[1] += 1
            elif not pred and label:
                s[2] += 1
        if (r.band != "excluded") != bool(label):
            failures.append(f"{'false alarm' if label == 0 else 'missed'}: {pid} × {cid} → {r.band} ({r.score}; {'; '.join(r.reasons)})")
    out = {"n": len(data["labels"]), "positives": sum(lab for *_, lab in data["labels"]), "failures": failures}
    for key, (tp, fp, fn) in stats.items():
        out[key] = dict(zip(("precision", "recall", "f1"), prf(tp, fp, fn)))
    return out


# --- faithfulness ---------------------------------------------------------------

def _demo_reports():
    out = []
    for d in DOCS:
        rep = diff_versions(renumber(d["v1"]), renumber(d["v2"]), major_update=True).to_dict()
        if rep["material"]:
            out.append((d["title"], rep))
    for line in (FIX / "changes.jsonl").read_text().splitlines():
        r = json.loads(line)
        rep = diff_versions(paras(r["old"]), paras(r["new"]), major_update=True).to_dict()
        if rep["material"]:
            out.append((r["id"], rep))
    return out


def _bump_money(text):
    m = re.search(r"£(\d[\d,]*(?:\.\d+)?)", text)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return text[:m.start()] + f"£{v + 5000:,.0f}" + text[m.end():]


def _shift_year(text):
    m = re.search(r"\b(20\d\d)\b", text)
    return text[:m.start()] + str(int(m.group(1)) + 1) + text[m.end():] if m else None


def _bump_number(text):
    m = re.search(r"\b(\d+) (days?|weeks?|months?|years?|people|tenants)\b", text)
    return text[:m.start()] + str(int(m.group(1)) + 7) + text[m.start() + len(m.group(1)):] if m else None


MUTATIONS = {
    "wrong_amount": lambda s, ev: _mutate_first(s, _bump_money),
    "shifted_date": lambda s, ev: _mutate_first(s, _shift_year),
    "wrong_period_or_count": lambda s, ev: _mutate_first(s, _bump_number),
    "advice_added": lambda s, ev: _append_claim(s, "You should act on this before the deadline.", s["claims"][0]["citations"]),
    "citation_removed": lambda s, ev: _set_cites(s, []),
    "bogus_citation": lambda s, ev: _set_cites(s, ["new:p999"]),
    "invented_claim": lambda s, ev: _append_claim(s, "Scottish distilleries now pay an extra licence surcharge annually.",
                                                  s["claims"][0]["citations"]),
    "quote_altered": lambda s, ev: _mutate_quote(s),
}


def _mutate_first(s, fn):
    s = copy.deepcopy(s)
    for c in s["claims"]:
        new = fn(c["text"])
        if new and new != c["text"]:
            c["text"] = new
            return s
    return None


def _append_claim(s, text, cites):
    s = copy.deepcopy(s)
    s["claims"].append({"text": text, "citations": list(cites)})
    return s


def _set_cites(s, cites):
    s = copy.deepcopy(s)
    s["claims"][0]["citations"] = cites
    return s


def _mutate_quote(s):
    s = copy.deepcopy(s)
    for c in s["claims"]:
        m = re.search(r'"([^"]{12,})"', c["text"])
        if m:
            q = m.group(1)
            words = q.split()
            for i, w in enumerate(words):
                if w.lower() in {"must", "can", "may", "should"}:
                    words[i] = {"must": "may", "can": "must", "may": "must", "should": "must"}[w.lower()]
                    break
            else:
                words.insert(min(3, len(words)), "never")
            c["text"] = c["text"].replace(q, " ".join(words))
            return s
    return None


def eval_faithfulness():
    reports = _demo_reports()
    clean_pass = 0
    clean_fail = []
    by_mut = {k: [0, 0] for k in MUTATIONS}  # detected, applicable
    for title, rep in reports:
        s = extractive(rep, title)
        if not s["claims"]:
            continue
        res = score_summary(s, rep)
        if res.score == 1.0:
            clean_pass += 1
        else:
            clean_fail.append(f"{title}: " + "; ".join(f"{c.text} → {c.problems}" for c in res.claims if not c.passed))
        for name, fn in MUTATIONS.items():
            bad = fn(s, None)
            if bad is None:
                continue
            by_mut[name][1] += 1
            if score_summary(bad, rep).score < 1.0:
                by_mut[name][0] += 1
    n_clean = clean_pass + len(clean_fail)
    det = {k: {"detected": d, "n": n, "rate": round(d / n, 3) if n else None} for k, (d, n) in by_mut.items()}
    total_d = sum(d for d, _ in by_mut.values())
    total_n = sum(n for _, n in by_mut.values())
    return {"faithful_summaries": n_clean, "false_alarm_rate": round(len(clean_fail) / n_clean, 3) if n_clean else None,
            "false_alarms": clean_fail, "hallucination_detection": det,
            "overall_detection_rate": round(total_d / total_n, 3) if total_n else None}


def eval_policy():
    advice = ["You should register now.", "We recommend you check your tenancy agreements.",
              "This means you must pay more.", "You are not affected by this change.", "Make sure you file on time."]
    neutral = ["The threshold changed from £85,000 to £90,000.", "The guidance now says landlords must protect deposits.",
               "The source does not say who is affected.", "The page was withdrawn."]
    caught = sum(bool(policy.advice_violations(t)) for t in advice)
    false = sum(bool(policy.advice_violations(t)) for t in neutral)
    amb = {"title": "Guidance", "kind": "amended", "report": {"paragraphs": [{"new_text": "Landlords must take reasonable steps where appropriate."}]}}
    clear = {"title": "Guidance", "kind": "amended", "report": {"paragraphs": [{"new_text": "The fee is £45."}]}}
    consult = {"title": "Consultation on HMO licensing fees", "kind": "new", "doc_type": "open_consultation", "report": {"paragraphs": []}}
    return {"advice_detected": f"{caught}/{len(advice)}", "advice_false_alarms": f"{false}/{len(neutral)}",
            "refuses_on_ambiguous_possible": policy.assess(amb, "possible")["refuse_applicability"],
            "no_refusal_on_clear_likely": not policy.assess(clear, "likely")["refuse_applicability"],
            "refuses_on_check_band": policy.assess(clear, "check")["refuse_applicability"],
            "labels_consultation_as_proposal": policy.assess(consult, "likely")["proposal"]}


def eval_llm():
    from regwatch.faithfulness import judge_with_llm
    from regwatch.summarise import llm
    rows = []
    for title, rep in _demo_reports()[:12]:
        s = llm(rep, title, "")
        if not s:
            rows.append({"title": title, "ok": False, "error": "no output"})
            continue
        res = score_summary(s, rep)
        verdicts = judge_with_llm(s, rep)
        entailed = sum(v["entailed"] for v in verdicts)
        rows.append({"title": title, "faithfulness": res.score, "passed_gate": res.passed,
                     "judge_entailed": f"{entailed}/{len(verdicts)}",
                     "problems": [p for c in res.claims for p in c.problems]})
    passed = sum(r.get("passed_gate", False) for r in rows)
    return {"n": len(rows), "passed_gate": passed, "rows": rows}


def main(llm: bool = False):
    results = {"change_detection": eval_change_detection(), "relevance": eval_relevance(),
               "faithfulness": eval_faithfulness(), "policy": eval_policy()}
    if llm:
        results["llm_summaries"] = eval_llm()
    (Path(__file__).with_name("report.json")).write_text(json.dumps(results, indent=2))
    md = render_md(results)
    (Path(__file__).with_name("report.md")).write_text(md)
    print(md)
    return results


def render_md(r):
    cd, rl, fa, po = r["change_detection"], r["relevance"], r["faithfulness"], r["policy"]
    lines = ["# RegWatch UK: offline evaluation report", "",
             "Generated by `python -m regwatch evals`. Fixtures are in `evals/fixtures/`. They are a small, hand-labelled seed "
             "set; grow them from users' \"not relevant\" and \"summary was wrong\" reports.", "",
             "## 1. Change detection (is this change material?)", "",
             f"| n | precision | recall | F1 | category accuracy |", "|---|---|---|---|---|",
             f"| {cd['n']} | {cd['precision']} | {cd['recall']} | {cd['f1']} | {cd['category_accuracy']} |", ""]
    lines += [f"- {f}" for f in cd["failures"]] or ["- No failures."]
    lines += ["", "## 2. Relevance ranking (should this profile see it?)", "",
              "| band | precision | recall | F1 |", "|---|---|---|---|",
              f"| any band (shown at all) | {rl['any']['precision']} | {rl['any']['recall']} | {rl['any']['f1']} |",
              f"| \"likely\" only | {rl['likely']['precision']} | {rl['likely']['recall']} | {rl['likely']['f1']} |", "",
              f"{rl['n']} labelled pairs, {rl['positives']} relevant.", ""]
    lines += [f"- {f}" for f in rl["failures"]] or ["- No failures."]
    lines += ["", "## 3. Faithfulness checker", "",
              f"Faithful (extractive) summaries checked: {fa['faithful_summaries']}. False-alarm rate: **{fa['false_alarm_rate']}**.", "",
              "Detection of injected hallucinations:", "", "| mutation | detected | rate |", "|---|---|---|"]
    lines += [f"| {k} | {v['detected']}/{v['n']} | {v['rate']} |" for k, v in fa["hallucination_detection"].items()]
    lines += ["", f"Overall detection rate: **{fa['overall_detection_rate']}**", ""]
    lines += [f"- false alarm: {f}" for f in fa["false_alarms"]]
    lines += ["", "## 4. Responsible-AI policy", ""] + [f"- {k.replace('_', ' ')}: **{v}**" for k, v in po.items()]
    if "llm_summaries" in r:
        ls = r["llm_summaries"]
        lines += ["", "## 5. Claude summaries", "", f"Passed faithfulness gate: {ls['passed_gate']}/{ls['n']}", "",
                  "| document | faithfulness | gate | judge entailed |", "|---|---|---|---|"]
        lines += [f"| {x['title']} | {x.get('faithfulness')} | {x.get('passed_gate')} | {x.get('judge_entailed')} |" for x in ls["rows"]]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main(llm="--llm" in sys.argv)
