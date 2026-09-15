"""Week 6 Task Set A: validate the ticket-reply judge before trusting its number.

    python main.py eval                              # THE one command (= run)

    python experiments/week6_eval.py draft           # 1. freeze 25 replies, commit
    python experiments/week6_eval.py label           # 2. YOU label them blind, commit
    python experiments/week6_eval.py judge --version v1          # 3. agreement_before
    #  4. write week6/prediction.txt (one sentence), commit
    python experiments/week6_eval.py make-judge-v2 --examples ID,ID   # 5. from v1's disagreements
    python experiments/week6_eval.py judge --version v2          # 6. agreement_after
    python experiments/week6_eval.py status          # ordering evidence

The protocol is enforced, not trusted:
  - `label` refuses once any judge result exists, and once labels are committed
  - `judge` refuses until the labels are committed and bound to the frozen replies
  - `make-judge-v2` and `judge --version v2` refuse until prediction.txt is committed
"""

import argparse
import gc
import json
import os
import random
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)                      # traces/ and week6/ are relative to the root

import tracing
from ticket_assertions import ASSERTIONS, run_assertions
from ticket_policy import ticket_block

W6 = ROOT / "week6"
CASES = W6 / "eval_cases.jsonl"
REPLIES = W6 / "replies_25.json"
LABELS = W6 / "labels_25.json"
PREDICTION = W6 / "prediction.txt"
AGREEMENT = W6 / "agreement.json"
RUNS = W6 / "runs"

LABELLED_N = 25
JUDGED_CRITERIA = ["resolution"]
JUDGE_VERSIONS = ("v1", "v2")

# Week 5 taxonomy (week5/taxonomy.md), verbatim.
MODES = {
    "M1": "Says \"I don't have enough information\" to a question the articles do answer",
    "M2": "A message that is not one direct question gets a flat refusal",
    "M3": "The reply stops mid-sentence",
    "M4": "The reply contains text that is not an answer — source headers, invented steps",
    "M5": "Answers \"what should I do\" without giving the steps",
}
MODE_SHORT = {
    "M1": "refuses a question the articles answer",
    "M2": "not one direct question -> refusal",
    "M3": "reply stops mid-sentence",
    "M4": "non-answer text / invented steps",
    "M5": "'what should I do' without steps",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def judge_prompt_path(version):
    return W6 / f"judge_{version}.txt"


def judge_results_path(version):
    return W6 / f"judge_results_{version}.json"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def file_sha(path):
    return tracing.sha256(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------

def load_cases():
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()
             if line.strip()]

    problems = []
    ids = [case["id"] for case in cases]
    if len(cases) < 25:
        problems.append(f"only {len(cases)} cases; the brief needs 25+")
    if len(set(ids)) != len(ids):
        problems.append("duplicate case ids")
    for case in cases:
        if case.get("mode") not in MODES:
            problems.append(f"{case['id']}: mode {case.get('mode')!r} is not a Week 5 mode")
        if not case.get("reference"):
            problems.append(f"{case['id']}: no reference facts")

    regressions = [case for case in cases if case.get("regression")]
    if len(regressions) < 2:
        problems.append(f"only {len(regressions)} regression cases; the brief needs 2+")

    known = {trace["trace_id"]: trace for trace in tracing.load_traces()}
    for case in regressions:
        source = known.get(case["regression"]["source_trace_id"])
        if source is None:
            print(f"  note: {case['id']} source trace not in local traces/ (gitignored) — "
                  f"cannot verify it is verbatim")
        elif source["query"] != case["ticket"]["message"]:
            problems.append(f"{case['id']}: message is not verbatim from trace "
                            f"{source['trace_id']}")

    if problems:
        raise SystemExit("eval_cases.jsonl is invalid:\n  " + "\n  ".join(problems))
    return cases


def labelled_cases(cases):
    return cases[:LABELLED_N]


# ---------------------------------------------------------------------------
# git evidence
# ---------------------------------------------------------------------------

def git(*args):
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def commits_of(path):
    """Every commit touching path, oldest first: [{commit, committed_at, subject}]."""
    rel = str(path.relative_to(ROOT))
    log = git("log", "--format=%h|%cI|%s", "--", rel)
    rows = [dict(zip(("commit", "committed_at", "subject"), line.split("|", 2)))
            for line in log.splitlines() if line]
    return list(reversed(rows))


def committed_clean(path):
    """The last commit of path, provided the file on disk is exactly that commit."""
    if not path.exists():
        return None
    rel = str(path.relative_to(ROOT))
    history = commits_of(path)
    if not history or git("status", "--porcelain", "--", rel):
        return None
    return history[-1]


def protocol_check(version):
    """(ok, problems, evidence) for running judge `version`."""
    problems, evidence = [], {}

    replies_commit = committed_clean(REPLIES)
    if not replies_commit:
        problems.append("week6/replies_25.json must exist and be committed (run `draft`, then commit)")
    evidence["replies_commit"] = replies_commit

    labels_commit = committed_clean(LABELS)
    if not labels_commit:
        problems.append("week6/labels_25.json must exist and be committed BEFORE the judge runs")
    else:
        labels = read_json(LABELS)
        if not labels.get("complete"):
            problems.append("labels_25.json is not complete — finish `label` first")
        if REPLIES.exists() and labels.get("replies_sha") != file_sha(REPLIES):
            problems.append("labels were made against a different replies_25.json")
        evidence["labels_commit"] = labels_commit
        evidence["labels_first_commit"] = commits_of(LABELS)[0]
        evidence["labels_commit_count"] = len(commits_of(LABELS))
        evidence["labels_updated_at"] = labels.get("updated_at")

    if not judge_prompt_path(version).exists():
        problems.append(f"week6/judge_{version}.txt does not exist")

    if version == "v2":
        if not committed_clean(judge_results_path("v1")):
            problems.append("judge v1 must have run and judge_results_v1.json be committed "
                            "(that is agreement_before)")
        prediction_commit = committed_clean(PREDICTION)
        if not prediction_commit:
            problems.append("week6/prediction.txt must be written and committed BEFORE iterating")
        evidence["prediction_commit"] = prediction_commit

    return not problems, problems, evidence


# ---------------------------------------------------------------------------
# replies
# ---------------------------------------------------------------------------

def draft_replies(cases, source, session_id):
    from generator import GENERATION_PARAMS
    from ticket_reply import draft_ticket_reply

    replies = []
    for index, case in enumerate(cases, start=1):
        tags = ["week6", f"case:{case['id']}", f"mode:{case['mode']}"]
        if case.get("regression"):
            tags.append("regression")
        result = draft_ticket_reply(case["ticket"], source=source,
                                    session_id=session_id, tags=tags)
        replies.append({
            "id": case["id"],
            "mode": case["mode"],
            "trace_id": result.get("trace_id"),
            "langfuse_trace_id": result.get("langfuse_trace_id"),
            "text": result["answer"],
            "refused": result["refused"],
            "refusal_reason": result["refusal_reason"],
            "output_tokens": result.get("output_tokens"),
            "max_new_tokens": GENERATION_PARAMS["max_new_tokens"],
            "retrieved": [chunk["chunk_id"] for chunk in result["retrieved"]],
        })
        state = f"refused:{result['refusal_reason'].split(' (')[0]}" if result["refused"] else "drafted"
        print(f"  {index:>2}/{len(cases)} {case['id']:<4} {case['mode']}  {state}")
    return replies


def release_generator():
    """Free the drafting model before loading the judge; 8 GB will not hold both."""
    import generator
    import ticket_reply
    generator._PIPELINE = None
    ticket_reply.release_ticket_pipeline()
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# draft
# ---------------------------------------------------------------------------

def cmd_draft(args):
    if LABELS.exists() and not args.force:
        print("labels_25.json already exists. Regenerating the replies would orphan "
              "those labels. (--force if you really are starting over.)")
        return 1

    from ticket_reply import (
        DEFAULT_RETRIEVAL_MODE,
        TICKET_MODEL_NAME,
        TICKET_PROMPT_VERSION,
        TICKET_SYSTEM_PROMPT,
    )

    cases = labelled_cases(load_cases())
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"Drafting {len(cases)} replies (Langfuse session week6-draft-{stamp})…")
    replies = draft_replies(cases, source="week6-draft", session_id=f"week6-draft-{stamp}")

    write_json(REPLIES, {
        "generated_at": now_iso(),
        "app_version": tracing.app_version(),
        "cases_sha": file_sha(CASES),
        "model": TICKET_MODEL_NAME,
        "retrieval_mode": DEFAULT_RETRIEVAL_MODE,
        "ticket_prompt_version": TICKET_PROMPT_VERSION,
        "ticket_prompt_sha": tracing.sha256(TICKET_SYSTEM_PROMPT),
        "replies": replies,
    })
    tracing.flush()

    print(f"\nFrozen: {REPLIES.relative_to(ROOT)}")
    print("Next:\n  git add week6/replies_25.json && git commit -m 'week6: freeze 25 replies for blind labelling'")
    print("  python experiments/week6_eval.py label")
    return 0


# ---------------------------------------------------------------------------
# label
# ---------------------------------------------------------------------------

def criterion_text():
    import judge
    return judge.load_prompt(judge_prompt_path("v1"))["system"]


def cmd_label(args):
    if any(W6.glob("judge_results_*.json")):
        print("A judge result already exists. Labels written now are not blind — "
              "you have seen (or could see) the judge's verdicts. Refusing.")
        return 1
    if committed_clean(LABELS) and read_json(LABELS).get("complete"):
        print("labels_25.json is complete and committed. Changing labels after the fact "
              "moves the ruler, not the thing measured. Refusing.")
        return 1
    if not committed_clean(REPLIES):
        print("Commit week6/replies_25.json first, so the labels bind to a fixed set:\n"
              "  git add week6/replies_25.json && git commit -m 'week6: freeze 25 replies'")
        return 1

    cases = {case["id"]: case for case in load_cases()}
    frozen = read_json(REPLIES)
    replies = frozen["replies"]

    if LABELS.exists():
        data = read_json(LABELS)
        if data.get("replies_sha") != file_sha(REPLIES):
            print("labels_25.json belongs to a different replies file. Move it aside first.")
            return 1
    else:
        data = {
            "criterion": "resolution (binary PASS/FAIL)",
            "criterion_source": "week6/judge_v1.txt (system part, shown verbatim while labelling)",
            "labeller": git("config", "user.name") or os.environ.get("USER"),
            "protocol": "blind: labelled from ticket + reference facts + reply only; no judge "
                        "result existed; case mode and regression flag hidden; order shuffled",
            "replies_file": "week6/replies_25.json",
            "replies_sha": file_sha(REPLIES),
            "replies_commit": committed_clean(REPLIES),
            "order_seed": 6,
            "started_at": now_iso(),
            "labels": {},
        }

    # Shuffled so that labelling order does not follow the mode grouping in the
    # case file — seeing seven M1 cases in a row primes you to expect failures.
    order = list(replies)
    random.Random(data["order_seed"]).shuffle(order)

    print("=" * 78)
    print("BLIND LABELLING — the judge's single criterion, verbatim:")
    print("=" * 78)
    print(criterion_text())
    print("=" * 78)

    for position, reply in enumerate(order, start=1):
        if reply["id"] in data["labels"]:
            continue
        case = cases[reply["id"]]
        print(f"\n\n########## {position}/{len(order)} ##########")
        print(ticket_block(case["ticket"]))
        print("\nREFERENCE FACTS")
        for fact in case["reference"]:
            print(f"  - {fact}")
        print("\nDRAFT REPLY")
        print("  " + (reply["text"] or "").replace("\n", "\n  "))

        while True:
            answer = input("\nRESOLUTION — [p]ass / [f]ail / [q]uit (saves progress): ").strip().lower()
            if answer in ("p", "f", "q"):
                break
        if answer == "q":
            break
        note = input("Why, in one line (reused if this case becomes a few-shot example): ").strip()

        data["labels"][reply["id"]] = {
            "label": "PASS" if answer == "p" else "FAIL",
            "note": note,
            "labelled_at": now_iso(),
        }
        data["updated_at"] = now_iso()
        data["complete"] = len(data["labels"]) == len(replies)
        write_json(LABELS, data)

    done = len(data["labels"])
    print(f"\n{done}/{len(replies)} labelled -> {LABELS.relative_to(ROOT)}")
    if data.get("complete"):
        counts = Counter(entry["label"] for entry in data["labels"].values())
        print(f"PASS {counts['PASS']} · FAIL {counts['FAIL']}")
        print("\nCommit NOW, before any judge run — this commit is the ordering evidence:")
        print("  git add week6/labels_25.json && git commit -m 'week6: 25 blind hand labels (before judge run)'")
    return 0


# ---------------------------------------------------------------------------
# judge + agreement
# ---------------------------------------------------------------------------

def few_shot_ids(version):
    path = W6 / f"judge_{version}_examples.json"
    return read_json(path)["examples"] if path.exists() else []


def agreement_stats(rows, exclude=()):
    def rate(subset):
        n = len(subset)
        agree = sum(1 for row in subset if row["agree"])
        return {"n": n, "agree": agree, "pct": round(100.0 * agree / n, 1) if n else None}

    n = len(rows)
    confusion = Counter(f"human_{row['human']}__judge_{row['judge']}" for row in rows)
    po = sum(1 for row in rows if row["agree"]) / n if n else 0
    human_pass = sum(1 for row in rows if row["human"] == "PASS") / n if n else 0
    judge_pass = sum(1 for row in rows if row["judge"] == "PASS") / n if n else 0
    pe = human_pass * judge_pass + (1 - human_pass) * (1 - judge_pass)

    return {
        "all": rate(rows),
        "held_out": rate([row for row in rows if row["id"] not in exclude]) if exclude else None,
        "excluded_few_shot": list(exclude),
        "confusion": dict(confusion),
        "cohen_kappa": round((po - pe) / (1 - pe), 3) if pe < 1 else None,
        "judge_unparsed": sum(1 for row in rows if not row["parsed"]),
    }


def run_judge(version, cases, replies_by_id, reply_trace_ids=None, labels=None):
    """Judge every case. Returns rows with verdicts (and agreement if labels)."""
    import judge

    prompt = judge.load_prompt(judge_prompt_path(version))
    rows = []
    for index, case in enumerate(cases, start=1):
        reply = replies_by_id[case["id"]]
        verdict = judge.judge_reply(prompt, case, reply["text"] or "")
        row = {
            "id": case["id"],
            "mode": case["mode"],
            "judge": verdict["verdict"],
            "parsed": verdict["parsed"],
            "judge_reason": verdict["reason"],
            "judge_raw": verdict["raw"],
        }
        if labels:
            row["human"] = labels[case["id"]]["label"]
            row["human_note"] = labels[case["id"]]["note"]
            row["agree"] = row["human"] == row["judge"]

        trace_id = (reply_trace_ids or {}).get(case["id"]) or reply.get("trace_id")
        if trace_id:
            tracing.langfuse_evaluation(
                trace_id, f"judge_{version}",
                input=verdict["messages"], output=verdict["raw"],
                metadata={"judge_model": judge.JUDGE_MODEL, "prompt_sha": prompt["sha"]},
            )
            tracing.langfuse_score(trace_id, f"judge_{version}", verdict["verdict"] == "PASS",
                                   comment=verdict["reason"])
            if labels:
                tracing.langfuse_score(trace_id, "human_label", row["human"] == "PASS",
                                       comment=row["human_note"] or None)

        mark = "" if not labels else ("  agree" if row["agree"] else f"  DISAGREE (human {row['human']})")
        print(f"  {index:>2}/{len(cases)} {case['id']:<4} judge {verdict['verdict']}"
              f"{'' if verdict['parsed'] else ' (unparsed)'}{mark}")
        rows.append(row)
    return rows, prompt


def print_agreement(version, stats, rows):
    a = stats["all"]
    print("\n" + "=" * 78)
    print(f"AGREEMENT judge_{version} vs hand labels: {a['agree']}/{a['n']} = {a['pct']}%"
          f"   (Cohen's kappa {stats['cohen_kappa']})")
    if stats["held_out"]:
        h = stats["held_out"]
        print(f"  held out (excluding the few-shot cases {', '.join(stats['excluded_few_shot'])}): "
              f"{h['agree']}/{h['n']} = {h['pct']}%")
    print(f"  confusion: {stats['confusion']}")
    if stats["judge_unparsed"]:
        print(f"  unparsed judge answers counted as FAIL: {stats['judge_unparsed']}")
    disagreements = [row for row in rows if not row["agree"]]
    print(f"\nDISAGREEMENTS ({len(disagreements)})")
    for row in disagreements:
        print(f"  {row['id']:<4} {row['mode']}  human {row['human']:<4} judge {row['judge']:<4}")
        print(f"       judge: {row['judge_reason'][:110]}")
        print(f"       human: {(row['human_note'] or '-')[:110]}")
    print("=" * 78)


def cmd_judge(args):
    version = args.version
    ok, problems, evidence = protocol_check(version)
    if not ok:
        print(f"JUDGE {version} NOT RUN — the protocol is not satisfied:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    import judge

    cases = labelled_cases(load_cases())
    frozen = read_json(REPLIES)
    replies_by_id = {reply["id"]: reply for reply in frozen["replies"]}
    labels = read_json(LABELS)["labels"]

    started = now_iso()
    print(f"Judge {version} ({judge.JUDGE_MODEL}) over the {len(cases)} frozen, labelled replies…")
    rows, prompt = run_judge(version, cases, replies_by_id, labels=labels)
    tracing.flush()

    stats = agreement_stats(rows, exclude=few_shot_ids(version))
    write_json(judge_results_path(version), {
        "version": version,
        "judge_prompt": f"week6/judge_{version}.txt",
        "judge_prompt_sha": prompt["sha"],
        "judge_model": judge.JUDGE_MODEL,
        "judge_params": judge.JUDGE_PARAMS,
        "run_started_at": started,
        "run_finished_at": now_iso(),
        "app_version": tracing.app_version(),
        "protocol_evidence": evidence,
        "agreement": stats,
        "rows": rows,
    })

    summary = read_json(AGREEMENT) if AGREEMENT.exists() else {}
    key = "agreement_before" if version == "v1" else "agreement_after"
    summary[key] = stats["all"]["pct"]
    summary[f"{key}_detail"] = {"judge": f"judge_{version}.txt", **stats}
    summary["assertion_count"] = len(ASSERTIONS)
    summary["judged_criteria_count"] = len(JUDGED_CRITERIA)
    write_json(AGREEMENT, summary)

    print_agreement(version, stats, rows)
    print(f"\nSaved {judge_results_path(version).relative_to(ROOT)} and {AGREEMENT.relative_to(ROOT)}")
    if version == "v1":
        print("\nNext, BEFORE touching the judge:")
        print("  1. read the disagreements above and write ONE sentence in week6/prediction.txt")
        print("     saying what giving the judge two of them as examples will fix")
        print("  2. git add week6/judge_results_v1.json week6/agreement.json week6/prediction.txt")
        print("     git commit -m 'week6: judge v1 agreement + prediction before iterating'")
        print("  3. python experiments/week6_eval.py make-judge-v2 --examples ID,ID")
    else:
        before = summary.get("agreement_before")
        print(f"\nagreement_before -> agreement_after: {before}% -> {stats['all']['pct']}%")
    return 0


def cmd_make_v2(args):
    if not committed_clean(PREDICTION):
        print("Write week6/prediction.txt and commit it first — the prediction has to "
              "predate the iteration.")
        return 1
    results_path = judge_results_path("v1")
    if not results_path.exists():
        print("Run `judge --version v1` first.")
        return 1

    import judge

    results = read_json(results_path)
    rows = {row["id"]: row for row in results["rows"]}
    ids = [value.strip() for value in args.examples.split(",") if value.strip()]
    reasons = dict(item.split("=", 1) for item in (args.reason or []))

    if len(ids) != 2:
        print("Give exactly two case ids.")
        return 1
    for case_id in ids:
        if case_id not in rows or rows[case_id]["agree"]:
            print(f"{case_id} is not one of judge v1's disagreements — the brief asks for the "
                  f"judge's OWN disagreements as examples.")
            return 1
        if not (reasons.get(case_id) or rows[case_id]["human_note"]):
            print(f"{case_id} has no labelling note; pass --reason {case_id}=\"why\"")
            return 1

    cases = {case["id"]: case for case in load_cases()}
    replies = {reply["id"]: reply for reply in read_json(REPLIES)["replies"]}
    v1 = judge.load_prompt(judge_prompt_path("v1"))

    blocks = [
        "WORKED EXAMPLES — replies you previously graded wrongly. Study why the "
        "correct verdict is correct, then grade the new reply the same way."
    ]
    for number, case_id in enumerate(ids, start=1):
        case, row = cases[case_id], rows[case_id]
        reference = "\n".join(f"- {fact}" for fact in case["reference"])
        blocks.append(
            f"--- EXAMPLE {number} (you answered {row['judge']}; the correct verdict is {row['human']}) ---\n"
            f"TICKET\n{ticket_block(case['ticket'])}\n\n"
            f"REFERENCE FACTS\n{reference}\n\n"
            f"DRAFT REPLY\n{replies[case_id]['text']}\n\n"
            f"VERDICT: {row['human']}\n"
            f"REASON: {reasons.get(case_id) or row['human_note']}"
        )

    text = (v1["system"] + "\n\n" + "\n\n".join(blocks) + "\n\n"
            + judge.USER_MARKER + "\n" + v1["user"] + "\n")
    judge_prompt_path("v2").write_text(text, encoding="utf-8")
    write_json(W6 / "judge_v2_examples.json", {
        "examples": ids,
        "source": "judge_results_v1.json disagreements",
        "reasons": {case_id: reasons.get(case_id) or rows[case_id]["human_note"] for case_id in ids},
        "created_at": now_iso(),
    })

    print(f"Wrote week6/judge_v2.txt with examples {', '.join(ids)}")
    print("Diff:  git diff --no-index week6/judge_v1.txt week6/judge_v2.txt")
    print("Then:  git add week6/judge_v2.txt week6/judge_v2_examples.json && git commit -m 'week6: judge v2'")
    print("       python experiments/week6_eval.py judge --version v2")
    return 0


# ---------------------------------------------------------------------------
# run — the one command
# ---------------------------------------------------------------------------

def best_available_judge():
    for version in reversed(JUDGE_VERSIONS):
        if protocol_check(version)[0]:
            return version
    return "none"


def print_table(cases, results, judge_version):
    by_mode = defaultdict(list)
    for case in cases:
        by_mode[case["mode"]].append(results[case["id"]])

    judged = judge_version != "none"
    jcol = f"judge {judge_version}" if judged else "judge"

    print("\n" + "=" * 92)
    print("PASS RATE BY MODE   case passes = every applicable assertion passes"
          + (" AND the judge says PASS" if judged else "  (judge not run)"))
    print("=" * 92)
    print(f"{'mode':<4}  {'Week 5 failure mode':<40} {'n':>3}  {'assertions':>10}  {jcol:>9}  {'pass':>6}  {'rate':>6}")
    print("-" * 92)

    def line(label, name, rows):
        n = len(rows)
        a = sum(1 for row in rows if row["assertions_pass"])
        j = sum(1 for row in rows if row.get("judge") == "PASS")
        p = sum(1 for row in rows if row["pass"])
        jtext = f"{j}/{n}" if judged else "-"
        print(f"{label:<4}  {name:<40} {n:>3}  {f'{a}/{n}':>10}  {jtext:>9}  {f'{p}/{n}':>6}  {100.0 * p / n:>5.1f}%")

    for mode in MODES:
        if by_mode[mode]:
            line(mode, MODE_SHORT[mode], by_mode[mode])
    print("-" * 92)
    line("ALL", "", [results[case["id"]] for case in cases])

    print("\nASSERTIONS (deterministic)          applies  pass  fail  failing cases")
    for name, _check, replaces in ASSERTIONS:
        applied = [(case["id"], a) for case in cases for a in results[case["id"]]["assertions"]
                   if a["name"] == name and a["applicable"]]
        failed = [case_id for case_id, a in applied if not a["passed"]]
        print(f"  {name:<32} {len(applied):>7}  {len(applied) - len(failed):>4}  {len(failed):>4}  "
              f"{', '.join(failed) or '-'}")

    print("\nREGRESSION CASES (replayed verbatim from failed Week 5 traces)")
    for case in cases:
        if case.get("regression"):
            row = results[case["id"]]
            now = "PASS" if row["pass"] else "FAIL"
            print(f"  {case['id']} <- {case['regression']['source_trace_id']}  {case['mode']}  "
                  f"was: {case['regression']['original_outcome']:<42} now: "
                  f"{now if judged else f'assertions {now} (resolution not judged)'}")

    print(f"\nCRITERIA SPLIT: {len(ASSERTIONS)} deterministic assertions · "
          f"{len(JUDGED_CRITERIA)} judged criterion ({', '.join(JUDGED_CRITERIA)})")
    print("  removed from the judge (judge_v0_presplit.txt -> judge_v1.txt): "
          + ", ".join(replaces for _n, _c, replaces in ASSERTIONS))


def cmd_run(args):
    cases = load_cases()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_id = f"week6-eval-{stamp}"

    print(f"Week 6 eval · {len(cases)} cases · "
          f"{sum(1 for c in cases if c.get('regression'))} regression · "
          f"modes {dict(Counter(c['mode'] for c in cases))}")

    frozen = read_json(REPLIES) if REPLIES.exists() else None
    frozen_by_id = {reply["id"]: reply for reply in (frozen or {}).get("replies", [])}

    if args.frozen:
        missing = [case["id"] for case in cases if case["id"] not in frozen_by_id]
        if missing:
            print(f"--frozen needs replies for every case; missing {missing}")
            return 1
        replies_by_id = frozen_by_id
        print("Replies: frozen week6/replies_25.json")
    else:
        print(f"Replies: drafting fresh (Langfuse session {session_id})")
        replies_by_id = {reply["id"]: reply
                         for reply in draft_replies(cases, "week6-eval", session_id)}
        release_generator()

    version = args.judge or best_available_judge()
    if version != "none":
        ok, problems, _ = protocol_check(version)
        if not ok:
            print(f"\nJUDGE {version} NOT RUN — protocol:")
            for problem in problems:
                print(f"  - {problem}")
            version = "none"
    if version == "none":
        print("\nJudge: not run (labels not committed yet, or --judge none). "
              "Assertions only.")

    results = {}
    for case in cases:
        assertions = run_assertions(case, replies_by_id[case["id"]])
        results[case["id"]] = {
            "id": case["id"],
            "mode": case["mode"],
            "assertions": assertions,
            "assertions_pass": all(a["passed"] for a in assertions if a["applicable"]),
        }

    labels = read_json(LABELS)["labels"] if LABELS.exists() and version != "none" else None
    if version != "none":
        print(f"\nJudging with judge_{version}.txt…")
        judge_rows, _ = run_judge(version, cases, replies_by_id)
        for row in judge_rows:
            results[row["id"]].update(judge=row["judge"], judge_reason=row["judge_reason"])

    for case in cases:
        row = results[case["id"]]
        row["pass"] = row["assertions_pass"] and (version == "none" or row.get("judge") == "PASS")
        trace_id = replies_by_id[case["id"]].get("trace_id")
        if trace_id:
            for a in row["assertions"]:
                if a["applicable"]:
                    tracing.langfuse_score(trace_id, f"assert:{a['name']}", a["passed"],
                                           comment=a["detail"])
            tracing.langfuse_score(trace_id, "eval_pass", row["pass"])
    tracing.flush()

    print_table(cases, results, version)

    if frozen_by_id and not args.frozen:
        drift = [case_id for case_id, reply in replies_by_id.items()
                 if case_id in frozen_by_id and reply["text"] != frozen_by_id[case_id]["text"]]
        print(f"\nReplies identical to the labelled frozen set: "
              f"{len(frozen_by_id) - len(drift)}/{len(frozen_by_id)}"
              + (f"  (differ: {', '.join(drift)})" if drift else ""))

    if labels:
        comparable = [cid for cid in labels
                      if cid in results and replies_by_id[cid]["text"] == frozen_by_id[cid]["text"]]
        agree = sum(1 for cid in comparable if results[cid].get("judge") == labels[cid]["label"])
        if comparable:
            print(f"Judge {version} vs hand labels on this run: {agree}/{len(comparable)} "
                  f"= {100.0 * agree / len(comparable):.1f}%")

    if AGREEMENT.exists():
        summary = read_json(AGREEMENT)
        print(f"\nagreement_before (judge v1): {summary.get('agreement_before')}%   "
              f"agreement_after (judge v2): {summary.get('agreement_after', 'not run yet')}"
              f"{'%' if 'agreement_after' in summary else ''}")

    write_json(RUNS / f"run_{stamp}.json", {
        "ran_at": now_iso(), "app_version": tracing.app_version(), "judge": version,
        "replies": "frozen" if args.frozen else "fresh", "session_id": session_id,
        "results": results,
    })
    print(f"\nSaved week6/runs/run_{stamp}.json")
    return 0


# ---------------------------------------------------------------------------
# status — ordering evidence
# ---------------------------------------------------------------------------

EVIDENCE_FILES = [
    ("eval_cases.jsonl", "25 mode-tagged cases"),
    ("judge_v0_presplit.txt", "judge before the assertion split"),
    ("judge_v1.txt", "judge after the split (1 criterion)"),
    ("replies_25.json", "frozen replies"),
    ("labels_25.json", "blind hand labels"),
    ("judge_results_v1.json", "judge v1 run -> agreement_before"),
    ("prediction.txt", "prediction before iterating"),
    ("judge_v2.txt", "judge with 2 of v1's disagreements"),
    ("judge_results_v2.json", "judge v2 run -> agreement_after"),
]


def protocol_evidence():
    """Commit-order evidence for every deliverable, as data for `status` and the UI."""
    rows, first = [], {}
    for name, what in EVIDENCE_FILES:
        path = W6 / name
        history = commits_of(path) if path.exists() else []
        if history:
            first[name] = history[0]
        rows.append({
            "file": name,
            "what": what,
            "first_commit": history[0]["commit"] if history else None,
            "committed_at": history[0]["committed_at"] if history else None,
            "commits": len(history),
            "on_disk": path.exists(),
            "clean": bool(committed_clean(path)),
        })

    # Commit times carry the local offset (+05:30) and run times are UTC, so
    # compare parsed instants: comparing the strings orders them wrongly.
    def instant(value):
        return datetime.fromisoformat(value)

    checks = []

    def order(a, b, claim):
        if a in first and b in first:
            ok = instant(first[a]["committed_at"]) <= instant(first[b]["committed_at"])
            checks.append({"status": "OK" if ok else "FAIL",
                           "text": f"{claim}: {first[a]['commit']} ({first[a]['committed_at']}) "
                                   f"before {first[b]['commit']} ({first[b]['committed_at']})"})
        else:
            checks.append({"status": "n/a", "text": f"{claim}: not both committed yet"})

    order("labels_25.json", "judge_results_v1.json", "labels committed before the judge v1 result")
    order("prediction.txt", "judge_v2.txt", "prediction committed before judge v2 exists")
    order("judge_v0_presplit.txt", "judge_v1.txt", "pre-split judge before the split judge")

    results_v1 = judge_results_path("v1")
    if results_v1.exists() and "labels_25.json" in first:
        run_at = read_json(results_v1)["run_started_at"]
        ok = instant(first["labels_25.json"]["committed_at"]) <= instant(run_at)
        checks.append({"status": "OK" if ok else "FAIL",
                       "text": f"labels commit time ({first['labels_25.json']['committed_at']}) "
                               f"before judge v1 run start ({run_at}, recorded inside "
                               f"judge_results_v1.json)"})
    if LABELS.exists():
        count = len(commits_of(LABELS))
        checks.append({"status": "OK" if count <= 1 else "WARN",
                       "text": f"labels_25.json committed {count} time(s)"
                               + ("" if count <= 1 else
                                  " — labels changed after their first commit; inspect git log")})

    return {"rows": rows, "checks": checks}


def protocol_steps():
    """The protocol as an ordered checklist, with the command for the next step."""
    labels = read_json(LABELS) if LABELS.exists() else {}
    labelled = len(labels.get("labels", {}))
    writeup = W6 / "disagreements.md"

    steps = [
        {"title": "Cases and assertion split committed",
         "detail": "eval_cases.jsonl · judge_v0_presplit.txt -> judge_v1.txt",
         "done": bool(committed_clean(judge_prompt_path("v1"))), "command": None},
        {"title": "25 replies frozen and committed",
         "detail": "week6/replies_25.json",
         "done": bool(committed_clean(REPLIES)),
         "command": "python experiments/week6_eval.py draft"},
        {"title": "Blind hand labels committed",
         "detail": f"{labelled}/{LABELLED_N} labelled · must be committed before any judge run",
         "done": bool(committed_clean(LABELS)) and bool(labels.get("complete")),
         "command": ("git add week6/labels_25.json && "
                     "git commit -m \"week6: 25 blind hand labels (before judge run)\""
                     if labels.get("complete") else "python experiments/week6_eval.py label")},
        {"title": "Judge v1 run -> agreement_before",
         "detail": "week6/judge_results_v1.json",
         "done": judge_results_path("v1").exists(),
         "command": "python experiments/week6_eval.py judge --version v1"},
        {"title": "Prediction committed before iterating",
         "detail": "one sentence in week6/prediction.txt",
         "done": bool(committed_clean(PREDICTION)),
         "command": ("# write week6/prediction.txt, then:\n"
                     "git add week6/judge_results_v1.json week6/agreement.json week6/prediction.txt && "
                     "git commit -m \"week6: judge v1 agreement + prediction\"")},
        {"title": "Judge v2 built from two of v1's disagreements",
         "detail": "week6/judge_v2.txt · judge_v2_examples.json",
         "done": judge_prompt_path("v2").exists(),
         "command": "python experiments/week6_eval.py make-judge-v2 --examples ID,ID"},
        {"title": "Judge v2 run -> agreement_after",
         "detail": "week6/judge_results_v2.json",
         "done": judge_results_path("v2").exists(),
         "command": ("git add week6/judge_v2.txt week6/judge_v2_examples.json && "
                     "git commit -m \"week6: judge v2\"\n"
                     "python experiments/week6_eval.py judge --version v2")},
        {"title": "Disagreement write-up",
         "detail": "2 disagreements, who was right, prediction scored against the outcome",
         "done": writeup.exists() and "__" not in writeup.read_text(encoding="utf-8"),
         "command": "edit week6/disagreements.md"},
    ]
    return steps, next((step for step in steps if not step["done"]), None)


def ui_artefacts():
    """Everything the UI's Judge validation view renders, read from week6/."""
    from judge import JUDGE_MODEL

    def text(path):
        return path.read_text(encoding="utf-8") if path.exists() else None

    runs = sorted(RUNS.glob("run_*.json")) if RUNS.exists() else []
    steps, next_step = protocol_steps()

    return {
        "cases": [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()
                  if line.strip()],
        "modes": MODES,
        "mode_short": MODE_SHORT,
        "assertions": [{"name": name, "replaces": replaces} for name, _check, replaces in ASSERTIONS],
        "judged_criteria": JUDGED_CRITERIA,
        "replies": read_json(REPLIES) if REPLIES.exists() else None,
        "latest_run": read_json(runs[-1]) if runs else None,
        "latest_run_file": runs[-1].name if runs else None,
        "labels": read_json(LABELS) if LABELS.exists() else None,
        "labels_commit": committed_clean(LABELS),
        "judge": {version: (read_json(judge_results_path(version))
                            if judge_results_path(version).exists() else None)
                  for version in JUDGE_VERSIONS},
        "judge_model": JUDGE_MODEL,
        "judge_allowed": best_available_judge(),
        "agreement": read_json(AGREEMENT) if AGREEMENT.exists() else None,
        "few_shot": few_shot_ids("v2"),
        "prompts": {"v0": text(W6 / "judge_v0_presplit.txt"),
                    "v1": text(judge_prompt_path("v1")),
                    "v2": text(judge_prompt_path("v2"))},
        "prediction": text(PREDICTION),
        "disagreements_md": text(W6 / "disagreements.md"),
        "readme_md": text(W6 / "README.md"),
        "protocol": protocol_evidence(),
        "steps": steps,
        "next_step": next_step,
    }


def ui_run(judge="none"):
    """The UI's Run eval button: the one command over the frozen replies.

    judge="auto" runs the newest judge the protocol allows — so the UI can
    never run a judge before the labels are committed, same as the CLI.
    """
    code = cmd_run(argparse.Namespace(frozen=True, judge=None if judge == "auto" else "none"))
    data = ui_artefacts()
    data["run_exit_code"] = code
    return data


def cmd_status(args):
    evidence = protocol_evidence()
    lines = ["# Week 6 — protocol evidence", "",
             f"Generated {now_iso()} by `python experiments/week6_eval.py status`.", "",
             "| file | what | first commit | committed at | commits | on disk matches HEAD |",
             "|---|---|---|---|---|---|"]
    for row in evidence["rows"]:
        lines.append(
            f"| `{row['file']}` | {row['what']} | "
            f"{('`' + row['first_commit'] + '`') if row['first_commit'] else '—'} | "
            f"{row['committed_at'] or '—'} | {row['commits']} | "
            f"{'yes' if row['clean'] else ('not on disk' if not row['on_disk'] else 'NO')} |"
        )

    checks = [f"- {check['status']:<4} {check['text']}" for check in evidence["checks"]]
    lines += ["", "## Ordering checks", ""] + checks
    text = "\n".join(lines) + "\n"
    (W6 / "protocol_evidence.md").write_text(text, encoding="utf-8")
    print(text)
    print("Written to week6/protocol_evidence.md")
    return 0


# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    draft = sub.add_parser("draft", help="draft and freeze the 25 replies")
    draft.add_argument("--force", action="store_true")
    draft.set_defaults(func=cmd_draft)

    label = sub.add_parser("label", help="blind hand labelling (interactive)")
    label.set_defaults(func=cmd_label)

    judge_cmd = sub.add_parser("judge", help="run a judge version over the frozen replies")
    judge_cmd.add_argument("--version", choices=JUDGE_VERSIONS, required=True)
    judge_cmd.set_defaults(func=cmd_judge)

    make_v2 = sub.add_parser("make-judge-v2", help="build judge_v2.txt from 2 of v1's disagreements")
    make_v2.add_argument("--examples", required=True, help="two case ids, e.g. T04,T12")
    make_v2.add_argument("--reason", action="append",
                         help='ID="why" when the labelling note is empty')
    make_v2.set_defaults(func=cmd_make_v2)

    add_run_arguments(sub.add_parser("run", help="the one command: pass rate by mode"))

    status = sub.add_parser("status", help="commit-order evidence")
    status.set_defaults(func=cmd_status)
    return parser


def add_run_arguments(parser):
    parser.add_argument("--judge", choices=JUDGE_VERSIONS + ("none",), default=None,
                        help="default: the newest judge the protocol allows")
    parser.add_argument("--frozen", action="store_true",
                        help="reuse week6/replies_25.json instead of drafting fresh")
    parser.set_defaults(func=cmd_run)


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    sys.exit(arguments.func(arguments))
