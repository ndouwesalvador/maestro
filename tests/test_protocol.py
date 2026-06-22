from maestro.protocol import Intervention, Plan, parse_edits, parse_summary


def test_parse_edits_single_block():
    text = (
        "foo.py\n"
        "<<<<<<< SEARCH\n"
        "old line\n"
        "=======\n"
        "new line\n"
        ">>>>>>> REPLACE\n"
        "SUMMARY: replaced it"
    )
    edits = parse_edits(text)
    assert len(edits) == 1
    assert edits[0].path == "foo.py"
    assert edits[0].search == "old line"
    assert edits[0].replace == "new line"


def test_parse_summary():
    assert parse_summary("blah\nSUMMARY: did the thing\n") == "did the thing"
    assert parse_summary("just one line") == "just one line"


def test_plan_from_text():
    plan = Plan.from_text(
        '{"steps":[{"id":"s1","title":"t","instruction":"i","check":"c"}]}', "the goal"
    )
    assert plan.goal == "the goal"
    assert len(plan.steps) == 1
    assert plan.steps[0].id == "s1"
    assert plan.steps[0].check == "c"


def test_plan_tolerates_code_fences():
    plan = Plan.from_text('```json\n{"steps":[{"instruction":"x","check":"y"}]}\n```', "g")
    assert plan.steps[0].instruction == "x"


def test_intervention_json_and_fallback():
    iv = Intervention.from_text('{"instruction":"use +","note":"oops"}')
    assert iv.instruction == "use +" and iv.note == "oops"
    fb = Intervention.from_text("plain text correction")
    assert fb.instruction == "plain text correction"
