"""Azure AI Language PII entity recognition surface (``/language/:analyze-text``)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.pii import RedactionPolicy, detect, redact
from tests.conftest import API_KEY, base_config

PII_PATH = "/language/:analyze-text?api-version=2026-05-01"
LANGUAGE_HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY, "content-type": "application/json"}


def pii_body(*texts: str, **parameters: Any) -> dict[str, Any]:
    return {
        "kind": "PiiEntityRecognition",
        "parameters": {"modelVersion": "latest", **parameters},
        "analysisInput": {
            "documents": [{"id": str(index + 1), "language": "en", "text": text} for index, text in enumerate(texts)]
        },
    }


def chat_json(content: str) -> str:
    return json.dumps({"messages": [{"role": "user", "content": content}], "max_tokens": 50})


@pytest.mark.contract("LANG-PII-ANALYZE")
def test_pii_response_shape_offsets_and_same_length_redaction(client):
    text = chat_json("Mail jane@contoso.com or call (555) 010-0199, SSN 123-45-6789.")
    response = client.post(PII_PATH, headers=LANGUAGE_HEADERS, json=pii_body(text))
    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "PiiEntityRecognitionResults"
    assert payload["results"]["errors"] == []
    assert payload["results"]["modelVersion"] == "2026-05-01"
    document = payload["results"]["documents"][0]
    assert document["id"] == "1"
    assert document["warnings"] == []
    categories = [entity["category"] for entity in document["entities"]]
    assert categories == ["Email", "PhoneNumber", "USSocialSecurityNumber"]
    for entity in document["entities"]:
        assert text[entity["offset"] : entity["offset"] + entity["length"]] == entity["text"]
        assert 0 < entity["confidenceScore"] <= 1
    redacted = document["redactedText"]
    assert len(redacted) == len(text)
    assert "jane@contoso.com" not in redacted and "*" * len("jane@contoso.com") in redacted
    # Only entity characters change, so a redacted JSON body is still JSON.
    assert json.loads(redacted)["max_tokens"] == 50
    differing = [index for index, (a, b) in enumerate(zip(text, redacted, strict=True)) if a != b]
    covered = {i for e in document["entities"] for i in range(e["offset"], e["offset"] + e["length"])}
    assert set(differing) == covered


@pytest.mark.contract("LANG-PII-ANALYZE")
def test_pii_accepts_preview_version_and_documents_without_entities(client):
    path = "/language/:analyze-text?api-version=2025-11-15-preview"
    response = client.post(path, headers=LANGUAGE_HEADERS, json=pii_body("Nothing personal here."))
    document = response.json()["results"]["documents"][0]
    assert document["entities"] == []
    assert document["redactedText"] == "Nothing personal here."


@pytest.mark.contract("LANG-PII-DETECTORS")
@pytest.mark.parametrize(
    ("text", "category", "value"),
    [
        ("reach me at a.b+tag@mail.example.co.uk today", "Email", "a.b+tag@mail.example.co.uk"),
        ("call +1 555-010-0199 now", "PhoneNumber", "+1 555-010-0199"),
        ("ring +44 20 7946 0958 please", "PhoneNumber", "+44 20 7946 0958"),
        ("card 4111 1111 1111 1111 exp", "CreditCardNumber", "4111 1111 1111 1111"),
        ("card 4111-1111-1111-1111 exp", "CreditCardNumber", "4111-1111-1111-1111"),
        ("ssn 078-05-1120 on file", "USSocialSecurityNumber", "078-05-1120"),
        ("NI number JG 12 34 56 C here", "UKNationalInsuranceNumber", "JG 12 34 56 C"),
        ("NI AB123456D", "UKNationalInsuranceNumber", "AB123456D"),
        ("host 192.168.10.254 is up", "IPAddress", "192.168.10.254"),
    ],
)
def test_regex_detectors(text, category, value):
    entities = detect(text)
    assert [(entity.category, entity.text) for entity in entities] == [(category, value)]
    assert entities[0].offset == text.index(value)


@pytest.mark.contract("LANG-PII-DETECTORS")
@pytest.mark.parametrize(
    "text",
    [
        "card 4111 1111 1111 1112 fails Luhn",
        "ssn 000-12-3456 and 666-12-3456 are never issued",
        "version 999.1.1.1 is not an address",
        "build 12345 finished",
        "specimen QQ 12 34 56 C is not issued",
        "not an email: user@localhost",
    ],
)
def test_detectors_reject_invalid_values(text):
    assert detect(text) == []


@pytest.mark.contract("LANG-PII-TRIGGERS")
def test_person_trigger_marks_every_occurrence(client):
    text = chat_json("My name is Jane Doe. [simulate:pii=Person:Jane Doe]")
    response = client.post(PII_PATH, headers=LANGUAGE_HEADERS, json=pii_body(text))
    document = response.json()["results"]["documents"][0]
    people = [entity for entity in document["entities"] if entity["category"] == "Person"]
    assert [entity["text"] for entity in people] == ["Jane Doe", "Jane Doe"]
    assert people[0]["confidenceScore"] == 1.0
    assert "Jane Doe" not in document["redactedText"]
    assert "[simulate:pii=Person:********]" in document["redactedText"]
    json.loads(document["redactedText"])


@pytest.mark.contract("LANG-PII-TRIGGERS")
def test_triggers_follow_simulation_trigger_switch(make_client):
    document = base_config()
    document["content_safety"]["simulation_triggers"] = False
    client = make_client(document)
    response = client.post(PII_PATH, headers=LANGUAGE_HEADERS, json=pii_body("Jane Doe [simulate:pii=Person:Jane Doe]"))
    assert response.json()["results"]["documents"][0]["entities"] == []
    # Unknown categories in a trigger are ignored rather than invented.
    assert detect("x [simulate:pii=Starship:Enterprise]") == []


@pytest.mark.contract("LANG-PII-CATEGORIES")
def test_pii_categories_filter(client):
    text = "jane@contoso.com 078-05-1120 10.0.0.1"
    response = client.post(
        PII_PATH, headers=LANGUAGE_HEADERS, json=pii_body(text, piiCategories=["Email", "ipaddress"])
    )
    document = response.json()["results"]["documents"][0]
    assert [entity["category"] for entity in document["entities"]] == ["Email", "IPAddress"]
    assert "078-05-1120" in document["redactedText"]
    unknown = client.post(PII_PATH, headers=LANGUAGE_HEADERS, json=pii_body(text, piiCategories=["Unicorn"]))
    assert unknown.status_code == 400
    assert unknown.json()["error"]["innererror"]["code"] == "InvalidParameterValue"
    bad_type = client.post(PII_PATH, headers=LANGUAGE_HEADERS, json=pii_body(text, piiCategories="Email"))
    assert bad_type.status_code == 400


@pytest.mark.contract("LANG-PII-REDACTION")
def test_redaction_policies(client):
    text = "jane@contoso.com and joe@contoso.com and jane@contoso.com, 078-05-1120"
    entity_mask = client.post(
        PII_PATH,
        headers=LANGUAGE_HEADERS,
        json=pii_body(text, redactionPolicies={"policyKind": "entityMask"}),
    ).json()["results"]["documents"][0]
    assert entity_mask["redactedText"] == "[EMAIL_1] and [EMAIL_2] and [EMAIL_1], [USSOCIALSECURITYNUMBER_1]"
    assert entity_mask["entities"][0]["offset"] == 0

    no_mask = client.post(
        PII_PATH, headers=LANGUAGE_HEADERS, json=pii_body(text, redactionPolicies={"policyKind": "noMask"})
    ).json()["results"]["documents"][0]
    assert "redactedText" not in no_mask
    assert len(no_mask["entities"]) == 4

    hashed = client.post(
        PII_PATH,
        headers=LANGUAGE_HEADERS,
        json=pii_body(text, redactionPolicies=[{"policyKind": "characterMask", "redactionCharacter": "#"}]),
    ).json()["results"]["documents"][0]
    assert hashed["redactedText"].startswith("#" * 16 + " and ")

    scoped = client.post(
        PII_PATH,
        headers=LANGUAGE_HEADERS,
        json=pii_body(
            text,
            redactionPolicies=[
                {"policyKind": "characterMask"},
                {"policyKind": "noMask", "entityTypes": ["USSocialSecurityNumber"]},
            ],
        ),
    ).json()["results"]["documents"][0]
    assert scoped["redactedText"].endswith(", 078-05-1120")
    assert scoped["redactedText"].startswith("*" * 16)

    for bad in (
        {"policyKind": "syntheticReplacement"},
        {"policyKind": "characterMask", "redactionCharacter": '"'},
        {"policyKind": "characterMask", "entityTypes": "Email"},
        [],
        "characterMask",
    ):
        response = client.post(PII_PATH, headers=LANGUAGE_HEADERS, json=pii_body(text, redactionPolicies=bad))
        assert response.status_code == 400, bad


def test_redact_helper_defaults_to_character_mask():
    entities = detect("x jane@contoso.com")
    assert redact("x jane@contoso.com", entities, []) == "x " + "*" * 16
    assert redact("x jane@contoso.com", entities, [RedactionPolicy(kind="noMask")]) is None


@pytest.mark.contract("LANG-PII-AUTH")
@pytest.mark.parametrize(
    "headers",
    [
        {"Ocp-Apim-Subscription-Key": API_KEY},
        {"api-key": API_KEY},
        {"Authorization": f"Bearer {API_KEY}"},
    ],
)
def test_language_auth_headers(client, headers):
    response = client.post(PII_PATH, headers=headers, json=pii_body("hello"))
    assert response.status_code == 200


@pytest.mark.contract("LANG-PII-AUTH")
def test_language_auth_rejections(client):
    missing = client.post(PII_PATH, json=pii_body("hello"))
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "401"
    wrong = client.post(PII_PATH, headers={"Ocp-Apim-Subscription-Key": "nope"}, json=pii_body("hello"))
    assert wrong.status_code == 401
    # The Language key header is not accepted on other data-plane surfaces.
    chat = client.post(
        "/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21",
        headers={"Ocp-Apim-Subscription-Key": API_KEY},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert chat.status_code == 401


@pytest.mark.contract("LANG-PII-VALIDATION")
def test_api_version_required_and_known(client):
    missing = client.post("/language/:analyze-text", headers=LANGUAGE_HEADERS, json=pii_body("hi"))
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "MissingApiVersionParameter"
    unknown = client.post(
        "/language/:analyze-text?api-version=2023-04-01", headers=LANGUAGE_HEADERS, json=pii_body("hi")
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "InvalidApiVersionParameter"
    assert unknown.headers["x-ms-error-code"] == "InvalidApiVersionParameter"


@pytest.mark.contract("LANG-PII-VALIDATION")
def test_request_validation(client):
    def post(body: Any) -> Any:
        return client.post(PII_PATH, headers=LANGUAGE_HEADERS, json=body)

    good = pii_body("hi")
    cases = [
        ([1, 2], "InvalidRequestBodyFormat"),
        ({**good, "kind": "EntityRecognition"}, "InvalidParameterValue"),
        ({**good, "parameters": []}, "InvalidParameterValue"),
        ({**good, "parameters": {"modelVersion": "2020-01-01"}}, "InvalidParameterValue"),
        ({**good, "analysisInput": {"documents": []}}, "MissingInputDocuments"),
        ({**good, "analysisInput": {}}, "MissingInputDocuments"),
        ({**good, "analysisInput": {"documents": ["text"]}}, "InvalidDocument"),
        ({**good, "analysisInput": {"documents": [{"id": "", "text": "x"}]}}, "InvalidDocument"),
        ({**good, "analysisInput": {"documents": [{"id": "1", "text": 5}]}}, "InvalidDocument"),
        ({**good, "analysisInput": {"documents": [{"id": "1", "text": "x", "language": 1}]}}, "InvalidDocument"),
        (
            {**good, "analysisInput": {"documents": [{"id": "1", "text": "a"}, {"id": "1", "text": "b"}]}},
            "InvalidParameterValue",
        ),
        (pii_body(*["x"] * 6), "InvalidDocumentBatch"),
    ]
    for body, inner_code in cases:
        response = post(body)
        assert response.status_code == 400, body
        error = response.json()["error"]
        assert error["code"] == "InvalidRequest"
        assert error["innererror"]["code"] == inner_code, body
    not_json = client.post(PII_PATH, headers=LANGUAGE_HEADERS, content=b"{nope")
    assert not_json.status_code == 400


@pytest.mark.contract("LANG-PII-LIMITS")
def test_document_size_limit_returns_per_document_error(client):
    too_long = "a" * 5_121
    exactly = "b" * 5_120
    response = client.post(PII_PATH, headers=LANGUAGE_HEADERS, json=pii_body(too_long, exactly, "   "))
    assert response.status_code == 200
    results = response.json()["results"]
    assert [document["id"] for document in results["documents"]] == ["2"]
    errors_by_id = {error["id"]: error["error"] for error in results["errors"]}
    assert set(errors_by_id) == {"1", "3"}
    assert errors_by_id["1"]["code"] == "InvalidArgument"
    assert errors_by_id["1"]["innererror"]["code"] == "InvalidDocument"
    assert "5120 text elements" in errors_by_id["1"]["innererror"]["message"]
    assert errors_by_id["3"]["innererror"]["message"] == "Document text is empty."
