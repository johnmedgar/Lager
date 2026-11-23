import asyncio
import logging
import pathlib
import types

import pytest

from lager import configure_lager, lager, load_log, note


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "log.yaml"
    configure_lager(path, mode="w")
    return path


def test_decorator_records_params_and_defaults(log_file):
    @lager
    def add(a, b=2, *rest, scale=1, **extra):
        return (a + b) * scale

    assert add(1, scale=3, note="hi") == 9

    (entry,) = load_log(log_file)
    assert entry["kind"] == "function"
    assert entry["function"] == "add"
    assert entry["params"] == {"a": 1, "b": 2, "scale": 3, "note": "hi"}


def test_wrap_without_decorator_and_options(log_file):
    def login(user, password, retries=3):
        return user

    wrapped = lager(login, exclude=["password"], include_defaults=False)
    assert wrapped("ed", "secret") == "ed"

    (entry,) = load_log(log_file)
    assert entry["params"] == {"user": "ed"}


def test_class_instantiation_and_methods(log_file):
    @lager
    class Model:
        def __init__(self, layers, dropout=0.1):
            self.layers = layers

        @lager
        def fit(self, data, epochs=5):
            return epochs

        @lager
        @classmethod
        def build(cls, size):
            return cls(size)

        @lager
        @staticmethod
        def helper(x):
            return x

    m = Model.build(3)
    m.fit([1, 2], epochs=10)
    Model.helper(path := pathlib.Path("a/b"))

    entries = load_log(log_file)
    assert [(e["kind"], e["qualname"]) for e in entries] == [
        ("method", "test_class_instantiation_and_methods.<locals>.Model.build"),
        ("class", "test_class_instantiation_and_methods.<locals>.Model"),
        ("method", "test_class_instantiation_and_methods.<locals>.Model.fit"),
        ("method", "test_class_instantiation_and_methods.<locals>.Model.helper"),
    ]
    assert entries[0]["params"] == {"size": 3}
    assert entries[1]["function"] == "Model"
    assert entries[1]["params"] == {"layers": 3, "dropout": 0.1}
    assert entries[2]["params"] == {"data": [1, 2], "epochs": 10}
    assert entries[3]["params"] == {"x": str(path)}


def test_class_without_init(log_file):
    @lager
    class Empty:
        pass

    Empty()
    (entry,) = load_log(log_file)
    assert entry["kind"] == "class" and entry["params"] == {}


def test_async_and_unserializable_values(log_file):
    class Thing:
        def __repr__(self):
            return "<Thing>"

    @lager
    async def fetch(obj, opts=None):
        return obj

    asyncio.run(fetch(Thing(), opts={"k": (1, 2)}))
    (entry,) = load_log(log_file)
    assert entry["params"] == {"obj": "<Thing>", "opts": {"k": [1, 2]}}


def test_append_mode_keeps_valid_yaml(tmp_path):
    path = tmp_path / "log.yaml"

    @lager
    def f(x):
        return x

    configure_lager(path, mode="w")
    f(1)
    configure_lager(path, mode="a")
    f(2)
    assert [e["params"]["x"] for e in load_log(path)] == [1, 2]


def test_note_writes_message_and_data_alongside_calls(log_file):
    @lager
    def filter_set(data, min_val=100):
        return data

    note("File loaded")
    filter_set("data")
    note("Job done", n_samples=9832, thresholds={"min_vals": (100, 200)}, out=pathlib.Path("a/b"))
    note(level=logging.WARNING, flagged=3)
    note("dropped", level=logging.DEBUG)  # below the configured INFO level

    entries = load_log(log_file)
    assert [e["kind"] for e in entries] == ["note", "function", "note", "note"]
    for e in (entries[0], entries[2], entries[3]):
        assert set(e) <= {"timestamp", "kind", "level", "message", "data"}
    assert entries[0]["level"] == "INFO"
    assert entries[0]["message"] == "File loaded"
    assert "data" not in entries[0]
    assert entries[2]["data"] == {
        "n_samples": 9832,
        "thresholds": {"min_vals": [100, 200]},
        "out": str(pathlib.Path("a/b")),
    }
    assert entries[3]["level"] == "WARNING"
    assert "message" not in entries[3]
    assert entries[3]["data"] == {"flagged": 3}
