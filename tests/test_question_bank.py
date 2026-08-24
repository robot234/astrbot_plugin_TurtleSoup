import json
import pathlib
import sys

import pytest


sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

from question_bank import RemoteQuestionBank


def test_parse_validates_deduplicates_and_marks_remote_questions():
    payload = json.dumps(
        [
            {"puzzle": "题面 A", "answer": "汤底 A"},
            {"puzzle": "题面 A", "answer": "汤底 A"},
            {"puzzle": "", "answer": "无效"},
            {
                "puzzle": "题面 B",
                "answer": "汤底 B",
                "title": "自定义标题",
                "difficulty": 99,
            },
            {"puzzle": "题面 C", "answer": "汤底 C", "title": [], "difficulty": "4"},
        ]
    ).encode()

    questions, stats = RemoteQuestionBank.parse(payload)

    assert stats == {"valid": 3, "invalid": 1, "duplicates": 1}
    assert questions[0][2]["id"] == "N001"
    assert questions[0][2]["source"] == "remote"
    assert questions[1][2]["title"] == "自定义标题"
    assert questions[1][2]["difficulty"] == 5
    assert questions[2][2]["title"] == "网络海龟汤"
    assert questions[2][2]["difficulty"] == 3


def test_parse_rejects_a_non_list_root():
    with pytest.raises(ValueError, match="JSON 数组"):
        RemoteQuestionBank.parse(b'{"puzzle":"x"}')


def test_cache_round_trip_and_age(tmp_path):
    cache = RemoteQuestionBank(tmp_path / "remote_questions.json")
    payload = '[{"puzzle":"题面","answer":"汤底"}]'.encode()

    cache.save_cache(payload)
    questions, stats = cache.load_cache()

    assert questions[0][0] == "题面"
    assert stats["valid"] == 1
    assert cache.cache_age_seconds() is not None


def test_download_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        RemoteQuestionBank.download("http://example.com/questions.json", 1)
