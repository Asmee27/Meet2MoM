"""
Synthetic evaluation meetings (Phase 2). Author-written, clearly synthetic.

Written BEFORE any dialogue-model output was inspected and never used for
training or threshold selection, so they act as a held-out synthetic check.
Results on them are always reported separately from real recordings.

    python -m evaluation.datasets.build_synthetic
writes evaluation/datasets/synthetic/<id>.transcript.json and
evaluation/annotations/synthetic_<id>.v1.json
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_T = ROOT / "evaluation" / "datasets" / "synthetic"
OUT_A = ROOT / "evaluation" / "annotations"

# Each meeting: lines are (speaker, text); items reference line indexes.
# item: (type, text, state/commitment, confirmed, evidence_idx, owner, owner_aliases, owner_type, deadline)
MEETINGS = {
    "hospital_shifts": {
        "domain": "healthcare scheduling",
        "lines": [
            ("Dr. Mehta", "Okay, the main issue this week is the night shift gap on Saturday."),
            ("Nurse Joy", "We are short two nurses from eleven to seven."),
            ("Dr. Mehta", "Could we pull one nurse from the day ward?"),
            ("Nurse Joy", "No, the day ward is already at minimum staffing, that won't work."),
            ("Admin Ravi", "What if we bring in agency staff for that night?"),
            ("Dr. Mehta", "Yes, let's do that, agency staff for Saturday night."),
            ("Nurse Joy", "Agreed."),
            ("Admin Ravi", "I'll call the agency and book two nurses by Thursday."),
            ("Dr. Mehta", "Great, thanks Ravi."),
            ("Nurse Joy", "Also, someone should update the on-call rota on the intranet."),
            ("Dr. Mehta", "We'll figure that out later."),
        ],
        "items": [
            ("decision", "Bring in agency staff for Saturday night", "accepted", True, [4, 5, 6], None, [], None, None),
            ("decision", "Pull a nurse from the day ward", "rejected", False, [2, 3], None, [], None, None),
            ("action", "Call the agency and book two nurses", "firm", True, [7, 8], "Admin Ravi", ["Admin Ravi", "Ravi", "SPEAKER_Admin Ravi"], "explicit_first_person", "by Thursday"),
            ("action", "Update the on-call rota on the intranet", "tentative", False, [9], None, [], "unassigned", None),
        ],
        "negatives": [([10], "Deferred ('We'll figure that out later')")],
    },
    "school_committee": {
        "domain": "school parent committee",
        "lines": [
            ("Principal Rao", "The annual day is on the fifteenth of December."),
            ("Mrs. Iyer", "I think we should move it to a weekend so more parents can come."),
            ("Mr. Das", "That depends on whether the auditorium is free on Saturday."),
            ("Principal Rao", "Right. If the auditorium is free, we'll move it to Saturday the fourteenth."),
            ("Mrs. Iyer", "Sounds reasonable."),
            ("Mr. Das", "I can check with the auditorium manager tomorrow."),
            ("Principal Rao", "Please do, and let us know by Friday."),
            ("Mrs. Iyer", "Should we also sell refreshments?"),
            ("Mr. Das", "Hmm, I'm not sure about that, maybe next year."),
        ],
        "items": [
            ("decision", "Move the annual day to Saturday the fourteenth if the auditorium is free", "conditional", False, [2, 3, 4], None, [], None, None),
            ("action", "Check auditorium availability with the manager", "firm", True, [5, 6], "Mr. Das", ["Mr. Das", "Das"], "explicit_first_person", "tomorrow"),
        ],
        "negatives": [([7, 8], "Refreshments: question and uncertain reply, nothing decided")],
    },
    "housing_society": {
        "domain": "housing society board",
        "lines": [
            ("Secretary Anil", "The lift in block B has broken down three times this month."),
            ("Treasurer Priya", "We have budget for either repairs or a full replacement, not both."),
            ("Member Kiran", "Replacement is safer in the long run."),
            ("Secretary Anil", "Let's go with replacement then."),
            ("Treasurer Priya", "Actually, wait. Replacement takes four months, and residents need the lift now."),
            ("Member Kiran", "True. So repair it now and plan the replacement for next financial year?"),
            ("Secretary Anil", "Yes, that's the better plan. Repair now, replacement next year."),
            ("Treasurer Priya", "Okay, agreed."),
            ("Secretary Anil", "Kiran, can you get three repair quotes by next Monday?"),
            ("Member Kiran", "Sure, I'll do that."),
        ],
        "items": [
            ("decision", "Repair the block B lift now and plan the replacement for next financial year", "accepted", True, [5, 6, 7], None, [], None, None),
            ("decision", "Replace the lift now", "rejected", False, [3, 4], None, [], None, None),
            ("action", "Get three repair quotes", "firm", True, [8, 9], "Member Kiran", ["Member Kiran", "Kiran"], "explicit_third_person", "by next Monday"),
        ],
        "negatives": [],
    },
    "product_team": {
        "domain": "software product team",
        "lines": [
            ("Lena", "Checkout conversion dropped eight percent after the last release."),
            ("Omar", "I think it's the new address form, it has too many required fields."),
            ("Lena", "Can we make the second address line optional?"),
            ("Omar", "Yes, that's easy."),
            ("Lena", "Okay, so the second address line becomes optional."),
            ("Priya", "We could also add Apple Pay."),
            ("Lena", "Maybe, but that's a bigger project, let's not commit to it today."),
            ("Omar", "I'll ship the address form fix in tomorrow's release."),
            ("Priya", "And I can write up an A/B test plan if that helps."),
        ],
        "items": [
            ("decision", "Make the second address line optional", "accepted", True, [2, 3, 4], None, [], None, None),
            ("decision", "Add Apple Pay", "proposed", False, [5, 6], None, [], None, None),
            ("action", "Ship the address form fix", "firm", True, [7], "Omar", ["Omar"], "explicit_first_person", "in tomorrow's release"),
            ("action", "Write an A/B test plan", "tentative", False, [8], "Priya", ["Priya"], "explicit_first_person", None),
        ],
        "negatives": [],
    },
    "research_lab": {
        "domain": "university research lab",
        "lines": [
            ("Prof. Chen", "The workshop deadline is the twentieth."),
            ("Aditi", "My experiments still need about two weeks."),
            ("Prof. Chen", "Hypothetically, if we dropped the ablation study, could you make it?"),
            ("Aditi", "Probably, but the paper would be weaker."),
            ("Marco", "I'd rather we aim for the main conference in March instead."),
            ("Prof. Chen", "Hmm. I'm not convinced yet. Let's decide next week once Aditi has preliminary results."),
            ("Aditi", "I'll send preliminary results by Wednesday."),
        ],
        "items": [
            ("decision", "Target the main conference in March instead of the workshop", "proposed", False, [4, 5], None, [], None, None),
            ("action", "Send preliminary results", "firm", True, [6], "Aditi", ["Aditi"], "explicit_first_person", "by Wednesday"),
        ],
        "negatives": [([2, 3], "Hypothetical ('if we dropped the ablation study')"), ([5], "Decision explicitly deferred")],
    },
    "event_planning": {
        "domain": "corporate event planning",
        "lines": [
            ("Sana", "For the offsite we have two venue options, the lakeside resort or the city hotel."),
            ("Tom", "The resort is nicer but it's two hours away."),
            ("Sana", "I'd prefer the city hotel, it's easier for everyone."),
            ("Tom", "Fine by me."),
            ("Grace", "Me too, city hotel."),
            ("Sana", "Great, we'll book the city hotel."),
            ("Tom", "Who's handling catering?"),
            ("Grace", "I can take catering."),
            ("Sana", "Perfect, thanks Grace. Could you have menu options by the end of the month?"),
            ("Grace", "Yes, end of the month works."),
            ("Tom", "Maybe we should also hire a photographer."),
        ],
        "items": [
            ("decision", "Book the city hotel for the offsite", "accepted", True, [2, 3, 4, 5], None, [], None, None),
            ("action", "Handle catering and prepare menu options", "firm", True, [7, 8, 9], "Grace", ["Grace"], "explicit_first_person", "by the end of the month"),
            ("decision", "Hire a photographer", "proposed", False, [10], None, [], None, None),
        ],
        "negatives": [([6], "Question")],
    },
    "finance_review": {
        "domain": "quarterly finance review",
        "lines": [
            ("CFO Nadia", "Marketing spend is twenty percent over budget this quarter."),
            ("Head of Marketing Leo", "Most of that is the trade show, which was approved in January."),
            ("CFO Nadia", "Understood. Going forward, any spend above ten thousand needs my sign-off."),
            ("Head of Marketing Leo", "That's fine."),
            ("CEO Arun", "Agreed, let's make that the policy from next month."),
            ("CFO Nadia", "Leo, please send me a revised forecast for Q4."),
            ("Head of Marketing Leo", "Will do, I'll have it ready by the end of next week."),
            ("CEO Arun", "We might also need to freeze hiring, but let's not decide that today."),
        ],
        "items": [
            ("decision", "Spend above ten thousand requires CFO sign-off from next month", "accepted", True, [2, 3, 4], None, [], None, None),
            ("action", "Send a revised Q4 forecast", "firm", True, [5, 6], "Head of Marketing Leo", ["Head of Marketing Leo", "Leo"], "explicit_third_person", "by the end of next week"),
        ],
        "negatives": [([7], "Hiring freeze explicitly not decided")],
    },
    "hinglish_standup": {
        "domain": "team stand-up (synthetic Hindi-English code-switching)",
        "lines": [
            ("Rohit", "Aaj ka main point hai login bug, users phir se complain kar rahe hain."),
            ("Sneha", "Mujhe lagta hai token expiry ka issue hai."),
            ("Rohit", "Toh kya hum expiry ko twenty four hours kar dein?"),
            ("Vikram", "Haan, theek hai, that should work for now."),
            ("Rohit", "Okay, decided, expiry twenty four hours kar dete hain."),
            ("Sneha", "Main kal tak fix deploy kar dungi."),
            ("Vikram", "Aur documentation bhi update karni padegi, but abhi koi owner nahi hai."),
            ("Rohit", "Agar time mila toh main dekh lunga, promise nahi kar sakta."),
        ],
        "items": [
            ("decision", "Set token expiry to twenty four hours", "accepted", True, [2, 3, 4], None, [], None, None),
            ("action", "Deploy the token expiry fix", "firm", True, [5], "Sneha", ["Sneha"], "explicit_first_person", "kal tak"),
            ("action", "Update the documentation", "tentative", False, [6, 7], None, [], "unassigned", None),
        ],
        "negatives": [],
    },
}


def build() -> list[str]:
    OUT_T.mkdir(parents=True, exist_ok=True)
    written = []
    for mid, m in MEETINGS.items():
        segments = []
        t = 0.0
        for i, (speaker, text) in enumerate(m["lines"]):
            dur = max(1.5, len(text.split()) * 0.35)
            segments.append({"utt_id": f"u{i:05d}", "start": round(t, 2), "end": round(t + dur, 2),
                             "speaker": speaker, "text": text,
                             "speaker_attribution_confidence": 0.9, "avg_logprob": -0.15})
            t += dur + 0.3
        uid = lambda idx: [f"u{j:05d}" for j in idx]
        transcript_path = OUT_T / f"{mid}.transcript.json"
        transcript_path.write_text(json.dumps({"synthetic": True, "segments": segments}, indent=1, ensure_ascii=False),
                                   encoding="utf-8")
        items = []
        for k, (typ, text, state, confirmed, ev, owner, aliases, owner_type, deadline) in enumerate(m["items"]):
            item = {"id": f"syn_{mid}.{typ[0]}{k + 1}", "type": typ, "text": text, "confirmed": confirmed,
                    "evidence": uid(ev)}
            if typ == "decision":
                item["state"] = state
            else:
                item.update({"commitment": state, "owner": owner, "owner_aliases": aliases,
                             "owner_type": owner_type, "deadline": deadline})
            items.append(item)
        annotation = {
            "schema": "meet2mom-gold-v1", "meeting_id": f"syn_{mid}", "version": 1, "synthetic": True,
            "domain": m["domain"], "split": "synthetic-test", "seen_during_development": False,
            "transcript": str(transcript_path.relative_to(ROOT)).replace("\\", "/"),
            "annotator": "Claude (author-written synthetic meeting and labels). Created 2026-10-16.",
            "items": items,
            "negatives": [{"evidence": uid(ev), "reason": reason} for ev, reason in m["negatives"]],
        }
        (OUT_A / f"synthetic_{mid}.v1.json").write_text(json.dumps(annotation, indent=1, ensure_ascii=False), encoding="utf-8")
        written.append(mid)
    return written


if __name__ == "__main__":
    print(build())
