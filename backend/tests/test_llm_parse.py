from services.llm import GemmaClient, parse_json


def test_plain_json():
    assert parse_json('{"a": 1}') == {"a": 1}


def test_code_fence():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_text_around_json():
    assert parse_json('Tabii: {"supported": false, "reason": "x"} bitti')["supported"] is False


def test_garbage_and_empty():
    assert parse_json("saçma") is None
    assert parse_json("") is None
    assert parse_json(None) is None
    assert parse_json("{broken") is None


def test_system_prompt_is_merged_into_prompt():
    assert GemmaClient._merge("soru", None) == "soru"
    merged = GemmaClient._merge("soru", "kurallar")
    assert merged.startswith("kurallar") and merged.endswith("soru")
