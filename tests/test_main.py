import asyncio
import importlib.util
import logging
import pathlib
import sys
import types

import pytest


def _decorator(*args, **kwargs):
    def wrap(func):
        return func

    return wrap


def _load_plugin_module():
    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = object
    api.logger = logging.getLogger("turtlesoup-tests")

    components = types.ModuleType("astrbot.api.message_components")
    components.Plain = lambda text: text

    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object
    event.MessageChain = list
    event.filter = types.SimpleNamespace(command=_decorator)

    provider = types.ModuleType("astrbot.api.provider")
    provider.LLMResponse = object

    star = types.ModuleType("astrbot.api.star")
    star.Context = object
    star.Star = object
    star.register = _decorator

    session_waiter = types.ModuleType("astrbot.core.utils.session_waiter")
    session_waiter.SessionController = object

    class SessionFilter:
        pass

    session_waiter.SessionFilter = SessionFilter
    session_waiter.session_waiter = _decorator

    sys.modules.update(
        {
            "astrbot": types.ModuleType("astrbot"),
            "astrbot.api": api,
            "astrbot.api.message_components": components,
            "astrbot.api.event": event,
            "astrbot.api.provider": provider,
            "astrbot.api.star": star,
            "astrbot.core": types.ModuleType("astrbot.core"),
            "astrbot.core.utils": types.ModuleType("astrbot.core.utils"),
            "astrbot.core.utils.session_waiter": session_waiter,
        }
    )

    module_path = pathlib.Path(__file__).parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location("turtlesoup_main", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


plugin_module = _load_plugin_module()


class FakeEvent:
    def __init__(self, sender_id, group_id=None):
        self.sender_id = sender_id
        self.group_id = group_id

    def get_sender_id(self):
        return self.sender_id

    def get_group_id(self):
        return self.group_id


class FakePlugin:
    @staticmethod
    def _get_session_key(event):
        return event.get_group_id() or event.get_sender_id()


class FakeProvider:
    def __init__(self, response="是", delay=0):
        self.response = response
        self.delay = delay
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        return types.SimpleNamespace(completion_text=self.response)


class ImmediateTimeoutProvider:
    async def text_chat(self, **kwargs):
        raise asyncio.TimeoutError


class FakeContext:
    def __init__(self, provider):
        self.provider = provider

    def get_using_provider(self):
        return self.provider

    def get_provider_by_id(self, provider_id):
        return self.provider if provider_id == "fast-judge" else None


class FakeQuestionEvent(FakeEvent):
    def __init__(self, sender_id, group_id=None):
        super().__init__(sender_id, group_id)
        self.sent = []

    def get_session_id(self):
        return "test-session"

    async def send(self, message):
        self.sent.append(message)


def _make_judge_plugin(provider, *, context_turns=3, timeout_seconds=15):
    plugin = object.__new__(plugin_module.TurtleSoupPlugin)
    plugin.context = FakeContext(provider)
    plugin.judge_provider_id = ""
    plugin.llm_context_turns = context_turns
    plugin.llm_timeout_seconds = timeout_seconds
    plugin.hint_system_prompt = "题目：{question}，答案：{answer}，明确作答：{is_answer_guess}"
    plugin.game_states = {}
    plugin.max_questions = 20
    plugin.session_timeout = 600
    return plugin


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("他是因为愧疚自杀的吗？", False),
        ("所以他是自杀吗？", False),
        ("答案是他因愧疚自杀", True),
        ("真相是他因愧疚自杀", True),
        ("我的答案是他因愧疚自杀", True),
        ("答案是", False),
    ],
)
def test_answer_guess_requires_explicit_declaration(question, expected):
    assert plugin_module.TurtleSoupPlugin._is_answer_guess(question) is expected


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("答对", "答对"),
        ("是", "是"),
        ("是。", "是"),
        ("不是", "请重新提问"),
        ("是否", "请重新提问"),
        ("玩家的猜测不是正确答案", "请重新提问"),
        ("", "请重新提问"),
    ],
)
def test_judge_response_accepts_only_known_labels(response, expected):
    assert plugin_module.TurtleSoupPlugin._validate_ai_response(
        object.__new__(plugin_module.TurtleSoupPlugin), response
    ) == expected


def test_session_filters_only_match_their_own_group():
    plugin = FakePlugin()
    group_one = plugin_module.TurtleSoupSessionFilter(plugin, "group-one")
    group_two = plugin_module.TurtleSoupSessionFilter(plugin, "group-two")

    event = FakeEvent(sender_id="user-a", group_id="group-one")

    assert group_one.filter(event) == group_one.session_id
    assert group_two.filter(event) == group_two.unmatched_session_id
    assert group_one.session_id != group_two.session_id


def test_session_filter_keeps_private_chats_sender_scoped():
    plugin = FakePlugin()
    private_session = plugin_module.TurtleSoupSessionFilter(plugin, "user-a")

    assert private_session.filter(FakeEvent(sender_id="user-a")) == private_session.session_id
    assert private_session.filter(FakeEvent(sender_id="user-b")) == private_session.unmatched_session_id


def test_judge_context_is_bounded_and_appended_only_after_success():
    provider = FakeProvider("否")
    plugin = _make_judge_plugin(provider, context_turns=1)
    game_state = {
        "question": "题面",
        "answer": "汤底",
        "llm_conversation_context": [
            {"role": "user", "content": "旧问题 1"},
            {"role": "assistant", "content": "旧回答 1"},
            {"role": "user", "content": "旧问题 2"},
            {"role": "assistant", "content": "旧回答 2"},
        ],
    }

    result = asyncio.run(plugin._get_ai_judge_response("新问题", game_state, "session", False))

    assert result == "否"
    assert len(provider.calls) == 1
    assert provider.calls[0]["contexts"] == [
        {"role": "system", "content": "题目：题面，答案：汤底，明确作答：否"},
        {"role": "user", "content": "旧问题 2"},
        {"role": "assistant", "content": "旧回答 2"},
        {"role": "user", "content": "新问题"},
    ]
    assert game_state["llm_conversation_context"][-2:] == [
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": "否"},
    ]


def test_normal_question_cannot_end_the_game_from_an_unexpected_correct_label():
    provider = FakeProvider("答对")
    plugin = _make_judge_plugin(provider)
    game_state = {"question": "题面", "answer": "汤底", "llm_conversation_context": []}

    result = asyncio.run(plugin._get_ai_judge_response("这是问题吗？", game_state, "session", False))

    assert result == "很接近了"


def test_explicit_guess_uses_one_classifier_request():
    provider = FakeProvider("否")
    plugin = _make_judge_plugin(provider)
    game_state = {
        "question": "题面",
        "answer": "汤底",
        "metadata": {},
        "question_count": 0,
        "llm_conversation_context": [],
        "controller": None,
    }
    plugin.game_states["group-a"] = game_state
    event = FakeQuestionEvent("user-a", "group-a")

    asyncio.run(plugin._handle_turtle_soup_question(event, "答案是另一种说法"))

    assert len(provider.calls) == 1
    assert provider.calls[0]["contexts"][0]["content"].endswith("明确作答：是")
    assert game_state["question_count"] == 1


def test_timeout_does_not_mutate_history():
    provider = FakeProvider("是", delay=2)
    plugin = _make_judge_plugin(provider, timeout_seconds=1)
    game_state = {"question": "题面", "answer": "汤底", "llm_conversation_context": []}

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(plugin._get_ai_judge_response("新问题", game_state, "session", False))

    assert game_state["llm_conversation_context"] == []


def test_timeout_does_not_consume_a_question():
    plugin = _make_judge_plugin(ImmediateTimeoutProvider())
    game_state = {
        "question": "题面",
        "answer": "汤底",
        "metadata": {},
        "question_count": 0,
        "llm_conversation_context": [],
        "controller": None,
    }
    plugin.game_states["group-a"] = game_state
    event = FakeQuestionEvent("user-a", "group-a")

    asyncio.run(plugin._handle_turtle_soup_question(event, "这是问题吗？"))

    assert game_state["question_count"] == 0
    assert event.sent[-1] == [plugin.MSG_AI_TIMEOUT]


def test_change_question_clears_history_without_rebuilding_a_system_prompt():
    plugin = _make_judge_plugin(FakeProvider())
    plugin.questions_bank = [
        ("新题面", "新汤底", {"id": "002", "title": "新题目", "difficulty": 2, "tags": []})
    ]
    game_state = {
        "question": "旧题面",
        "answer": "旧汤底",
        "metadata": {"id": "001", "title": "旧题目", "difficulty": 1, "tags": []},
        "question_count": 4,
        "llm_conversation_context": [{"role": "user", "content": "旧问题"}],
        "controller": None,
    }
    plugin.game_states["group-a"] = game_state
    event = FakeQuestionEvent("user-a", "group-a")

    asyncio.run(plugin.change_question(event))

    assert game_state["question"] == "新题面"
    assert game_state["answer"] == "新汤底"
    assert game_state["question_count"] == 0
    assert game_state["llm_conversation_context"] == []
    assert "换题成功" in event.sent[-1][0]
