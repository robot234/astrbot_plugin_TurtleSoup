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
        ("是", True),
        ("是。", True),
        ("`是`", True),
        ("否", False),
        ("不是", False),
        ("是否", False),
        ("玩家的猜测不是正确答案", False),
        ("", False),
    ],
)
def test_answer_check_requires_exact_positive_response(response, expected):
    assert plugin_module.TurtleSoupPlugin._is_positive_answer_check_response(response) is expected


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
