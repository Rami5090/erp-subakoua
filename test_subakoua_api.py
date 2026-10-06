import document_optimizer as d
from subakoua_api import SubakouaAPIClient, StudyFetchResult


def test_api_period_helpers():
    assert SubakouaAPIClient.period_code("Année 1 - Janvier") == "0101"
    assert SubakouaAPIClient.period_code("0106") == "0106"
    assert SubakouaAPIClient.month_id("Année 1 - Juin") == 18
    assert SubakouaAPIClient.period_label("0106") == "Année 1 - Juin"


def test_known_endpoint_for_audit_studies():
    class DummyContext:
        request = None
    client = object.__new__(SubakouaAPIClient)
    client.context = DummyContext()
    assert client.endpoint_for("bankStatements").endswith("company/bankStatements")
    assert client.endpoint_for("enprgeqtatdb").endswith("company/timeConsumption")
    assert client.endpoint_for("unknown") is None


def test_build_study_endpoint_replaces_placeholders():
    class DummyContext:
        request = None
    class DummyPlayer:
        session = "sess"
        team_id = "team"
    client = object.__new__(SubakouaAPIClient)
    client.context = DummyContext()
    client.request = None
    client.base_url = "https://subakoua.arkhe.com"
    client.player = DummyPlayer()
    url = client.build_study_endpoint("bankStatements", "Année 1 - Juin")
    assert "/sessions/sess/months/18/teams/team/" in url


def test_read_allowed_gate():
    bad = StudyFetchResult("study", "Année 1 - Juin", "0106", "url", False, {"studyId":"study", "readAllowed":False}, False, "denied")
    good = StudyFetchResult("study", "Année 1 - Juin", "0106", "url", True, {"studyId":"study", "readAllowed":True}, True, "")
    assert not bad.valid
    assert good.valid
