"""Reading a hiring request without a model."""

from tests.conftest import REQUEST

from enterprise_workflow.layer4_agents.step2_agents import (
    find_name,
    find_salary,
    intake_offline,
    name_from_email,
    parse_json_object,
    run_intake,
    run_policy_explanation,
    run_welcome_draft,
)


class FakeReply:
    def __init__(self, text):
        self.text = text
        self.input_tokens = 10
        self.output_tokens = 5
        self.model = "fake"


class FakeClient:
    def __init__(self, text):
        self.text = text
        self.prompts = []

    def complete(self, system_prompt, user_prompt, max_tokens=900):
        self.prompts.append(user_prompt)
        return FakeReply(self.text)


class BrokenClient:
    def complete(self, system_prompt, user_prompt, max_tokens=900):
        from enterprise_workflow.layer4_agents.step1_client import ModelUnavailable
        raise ModelUnavailable("no credit", "no_credit")


def test_the_offline_reader_gets_everything_from_an_ordinary_request():
    result = intake_offline(REQUEST)
    assert result.fields["full_name"] == "Ada Lovelace"
    assert result.fields["email"] == "ada.lovelace@example.com"
    assert result.fields["department"] == "engineering"
    assert result.fields["start_date"] == "2026-11-03"
    assert result.fields["salary"] == 95000.0
    assert result.missing == []


def test_a_name_is_taken_from_the_email_first():
    assert name_from_email("ada.lovelace@example.com") == "Ada Lovelace"
    assert name_from_email("ada@example.com") == ""
    assert name_from_email("not an email") == ""


def test_a_sentence_starter_is_not_part_of_the_name():
    # "Hiring Ada Lovelace as a backend engineer" produced a new joiner called
    # "Hiring Ada Lovelace", which would then be the name on their payroll record.
    assert find_name("Hiring Ada Lovelace as a backend engineer.", None) == "Ada Lovelace"
    assert find_name("Please onboard Grace Hopper next month.", None) == "Grace Hopper"
    assert find_name("nothing here", None) is None


def test_a_plainly_written_salary_is_found():
    # "Salary is 95000" has no currency symbol and no comma grouping, and is
    # exactly how people write it.
    assert find_salary("Salary is 95000.") == 95000.0
    assert find_salary("Salary is £95,000.") == 95000.0
    assert find_salary("salary: 82,500") == 82500.0
    assert find_salary("salary at 60000") == 60000.0
    assert find_salary("no numbers here") is None


def test_the_labelled_salary_wins_over_a_stray_amount():
    text = "Equipment budget is £2,400. Salary is 71000."
    assert find_salary(text) == 71000.0


def test_a_missing_field_is_reported_not_guessed():
    result = intake_offline("Somebody is joining soon.")
    assert "salary" in result.missing
    assert "start_date" in result.missing
    assert result.fields["salary"] is None


def test_the_model_is_used_when_there_is_one():
    client = FakeClient('{"full_name":"Grace Hopper","email":"g@example.com",'
                        '"department":"engineering","start_date":"2026-12-01",'
                        '"salary":120000,"equipment":["laptop"],"manager":"Ada"}')
    result = run_intake("anything", client)
    assert result.source == "model"
    assert result.fields["full_name"] == "Grace Hopper"
    assert result.fields["salary"] == 120000


def test_a_model_failure_falls_back_rather_than_failing_the_step():
    result = run_intake(REQUEST, BrokenClient())
    assert result.source == "offline"
    assert result.fields["full_name"] == "Ada Lovelace"
    assert "unavailable" in result.note


def test_an_unreadable_reply_falls_back():
    result = run_intake(REQUEST, FakeClient("I cannot do that."))
    assert result.source == "offline"
    assert "not JSON" in result.note


def test_json_parsing_survives_fences_and_prose():
    assert parse_json_object('```json\n{"a":1}\n```') == {"a": 1}
    assert parse_json_object('Sure:\n{"a":1}\nhope that helps') == {"a": 1}
    assert parse_json_object("no json at all") == {}


def test_the_policy_sentence_works_without_a_model():
    sentence = run_policy_explanation("Ada", "engineering", ["laptop"], 2500.0,
                                      2000.0, client=None)
    assert "2500.00" in sentence
    assert "2000.00" in sentence


def test_the_welcome_note_works_without_a_model():
    note = run_welcome_draft("Ada", "engineering", "2026-11-03", ["laptop"], client=None)
    assert "Ada" in note
    assert "2026-11-03" in note


def test_agents_never_touch_the_database():
    # An agent with state is a thing that can be lost when a process dies. They
    # take text and return text, and that is what makes a killed worker safe.
    import inspect

    from enterprise_workflow.layer4_agents import step2_agents

    source = inspect.getsource(step2_agents)
    assert "store." not in source
    assert "WorkflowStore" not in source
