import datetime as dt
import importlib
import inspect
import logging
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest
import yaml

from lager import (
    DEFAULT_LOG_FILE,
    LOGGER_NAME,
    configure_lager,
    get_logger,
    lager,
    load_log,
    note,
)

lager_module = importlib.import_module("lager.lager")


@pytest.fixture(autouse=True)
def clean_logger() -> Iterator[None]:
    """Detach and close every handler so tests never share logging state."""

    def reset() -> None:
        logger = logging.getLogger(LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(logging.NOTSET)

    reset()
    yield
    reset()


def lager_handlers() -> list[logging.Handler]:
    """Return the handlers installed by ``configure_lager``."""
    logger = logging.getLogger(LOGGER_NAME)
    return [h for h in logger.handlers if getattr(h, "_lager_handler", False)]


@pytest.fixture
def log_file(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "log.yaml"
    configure_lager(path, mode="w")
    return path


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --------------------------------------------------------------------------- #
# Functions
# --------------------------------------------------------------------------- #
def test_decorator_records_params_and_defaults(log_file: pathlib.Path) -> None:
    @lager
    def add(a, b=2, *rest, scale=1, **extra):
        return (a + b) * scale

    assert add(1, scale=3, note="hi") == 9

    (entry,) = load_log(log_file)
    assert entry["kind"] == "function"
    assert entry["function"] == "add"
    assert entry["params"] == {"a": 1, "b": 2, "scale": 3, "note": "hi"}


def test_var_positional_args_are_recorded_as_list(log_file: pathlib.Path) -> None:
    @lager
    def collect(first, *rest):
        return rest

    assert collect(1, 2, 3) == (2, 3)
    collect(1)

    with_rest, without_rest = load_log(log_file)
    assert with_rest["params"] == {"first": 1, "rest": [2, 3]}
    assert without_rest["params"] == {"first": 1}


def test_wrap_without_decorator_and_options(log_file: pathlib.Path) -> None:
    def login(user, password, retries=3):
        return user

    wrapped = lager(login, exclude=["password"], include_defaults=False)
    assert wrapped("ed", "secret") == "ed"

    (entry,) = load_log(log_file)
    assert entry["params"] == {"user": "ed"}


def test_exclude_applies_to_keyword_and_default_values(
    log_file: pathlib.Path,
) -> None:
    @lager(exclude=["token", "retries"])
    def call(url, token="abc", retries=3):
        return url

    call("u", token="secret")

    (entry,) = load_log(log_file)
    assert entry["params"] == {"url": "u"}
    assert "secret" not in log_file.read_text(encoding="utf-8")


def test_wrapper_preserves_metadata_and_return_value() -> None:
    def documented(x: int) -> int:
        """Docstring."""
        return x * 2

    wrapped = lager(documented)

    assert wrapped.__name__ == "documented"
    assert wrapped.__doc__ == "Docstring."
    assert wrapped(4) == 8


def test_exceptions_from_wrapped_function_propagate(log_file: pathlib.Path) -> None:
    @lager
    def boom(x):
        raise ValueError(x)

    with pytest.raises(ValueError, match="bad"):
        boom("bad")

    (entry,) = load_log(log_file)
    assert entry["params"] == {"x": "bad"}


def test_invalid_call_is_recorded_and_original_error_raised(
    log_file: pathlib.Path,
) -> None:
    @lager
    def needs_two(a, b):
        return a + b

    with pytest.raises(TypeError, match="missing 1 required positional"):
        needs_two(1)

    (entry,) = load_log(log_file)
    assert entry["params"] == {"arg0": 1}


def test_recording_failure_never_breaks_wrapped_call(
    log_file: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_emit(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("emit failed")

    monkeypatch.setattr(lager_module, "_emit", broken_emit)

    @lager
    def double(x):
        return x * 2

    assert double(2) == 4


def test_builtin_without_signature_records_positional_args(
    log_file: pathlib.Path,
) -> None:
    wrapped = lager(max)  # max has no introspectable signature on some versions
    assert wrapped(1, 5, 3) == 5

    (entry,) = load_log(log_file)
    assert entry["function"] == "max"
    assert 5 in entry["params"].values()


def test_lager_rejects_non_callable() -> None:
    with pytest.raises(TypeError, match="cannot wrap object of type int"):
        lager(42)


# --------------------------------------------------------------------------- #
# Classes and methods
# --------------------------------------------------------------------------- #
def test_class_instantiation_and_methods(log_file: pathlib.Path) -> None:
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

    path = pathlib.Path("a/b")
    m = Model.build(3)
    assert m.fit([1, 2], epochs=10) == 10
    assert Model.helper(path) == path

    prefix = "test_class_instantiation_and_methods.<locals>.Model"
    entries = load_log(log_file)
    assert [(e["kind"], e["qualname"]) for e in entries] == [
        ("method", f"{prefix}.build"),
        ("class", prefix),
        ("method", f"{prefix}.fit"),
        ("method", f"{prefix}.helper"),
    ]
    assert entries[0]["params"] == {"size": 3}
    assert entries[1]["function"] == "Model"
    assert entries[1]["params"] == {"layers": 3, "dropout": 0.1}
    assert entries[2]["params"] == {"data": [1, 2], "epochs": 10}
    assert entries[3]["params"] == {"x": str(path)}


def test_class_without_init(log_file: pathlib.Path) -> None:
    @lager
    class Empty:
        pass

    Empty()
    (entry,) = load_log(log_file)
    assert entry["kind"] == "class" and entry["params"] == {}


def test_class_with_new_but_no_init_uses_class_signature(
    log_file: pathlib.Path,
) -> None:
    @lager
    class Point:
        def __new__(cls, x, y=0):
            return super().__new__(cls)

    Point(1, y=2)

    (entry,) = load_log(log_file)
    assert entry["kind"] == "class"
    assert entry["params"] == {"x": 1, "y": 2}


def test_class_exclude_hides_secret_parameters(log_file: pathlib.Path) -> None:
    @lager(exclude=["password"])
    class Client:
        def __init__(self, user, password):
            self.user = user

    Client("ed", "hunter2")

    (entry,) = load_log(log_file)
    assert entry["params"] == {"user": "ed"}


def test_wrapping_class_twice_logs_once(log_file: pathlib.Path) -> None:
    class Once:
        def __init__(self, x):
            self.x = x

    lager(lager(Once))(1)

    assert len(load_log(log_file)) == 1


def test_subclass_instantiation_records_both_classes(
    log_file: pathlib.Path,
) -> None:
    @lager
    class Base:
        def __init__(self, a):
            self.a = a

    @lager
    class Child(Base):
        def __init__(self, a, b):
            super().__init__(a)
            self.b = b

    Child(1, 2)

    entries = load_log(log_file)
    assert [e["function"] for e in entries] == ["Child", "Base"]
    assert entries[0]["params"] == {"a": 1, "b": 2}
    assert entries[1]["params"] == {"a": 1}


# --------------------------------------------------------------------------- #
# Async
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_async_and_unserializable_values(log_file: pathlib.Path) -> None:
    class Thing:
        def __repr__(self) -> str:
            return "<Thing>"

    @lager
    async def fetch(obj, opts=None):
        return obj

    thing = Thing()
    assert await fetch(thing, opts={"k": (1, 2)}) is thing

    (entry,) = load_log(log_file)
    assert entry["params"] == {"obj": "<Thing>", "opts": {"k": [1, 2]}}


def test_async_wrapper_remains_coroutine_function() -> None:
    @lager
    async def work() -> None:
        return None

    assert inspect.iscoroutinefunction(work)


# --------------------------------------------------------------------------- #
# Value conversion
# --------------------------------------------------------------------------- #
def test_values_are_converted_to_safe_yaml(log_file: pathlib.Path) -> None:
    @lager
    def f(**kwargs):
        return None

    f(
        day=dt.date(2026, 1, 2),
        clock=dt.time(3, 4),
        raw=b"hi",
        items=frozenset({7}),
        mapping={(1, 2): "tuple key", "ok": None},
    )

    (entry,) = load_log(log_file)
    assert entry["params"] == {
        "day": "2026-01-02",
        "clock": "03:04:00",
        "raw": "hi",
        "items": [7],
        "mapping": {"(1, 2)": "tuple key", "ok": None},
    }


def test_deeply_nested_values_are_truncated_but_loadable(
    log_file: pathlib.Path,
) -> None:
    nested: Any = "leaf"
    for _ in range(50):
        nested = [nested]

    note(deep=nested)

    (entry,) = load_log(log_file)
    value = entry["data"]["deep"]
    depth = 0
    while isinstance(value, list):
        value = value[0]
        depth += 1
    assert depth < 50
    assert isinstance(value, str)


# --------------------------------------------------------------------------- #
# Logging configuration
# --------------------------------------------------------------------------- #
def test_append_mode_keeps_valid_yaml(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "log.yaml"

    @lager
    def f(x):
        return x

    configure_lager(path, mode="w")
    f(1)
    configure_lager(path, mode="a")
    f(2)
    assert [e["params"]["x"] for e in load_log(path)] == [1, 2]


def test_write_mode_overwrites_existing_log(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "log.yaml"

    @lager
    def f(x):
        return x

    configure_lager(path, mode="w")
    f(1)
    configure_lager(path, mode="w")
    f(2)
    assert [e["params"]["x"] for e in load_log(path)] == [2]


def test_reconfigure_replaces_lager_handlers_only(tmp_path: pathlib.Path) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    foreign = logging.NullHandler()
    logger.addHandler(foreign)

    configure_lager(tmp_path / "a.yaml")
    configure_lager(tmp_path / "b.yaml")

    file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    assert foreign in logger.handlers
    assert [pathlib.Path(h.baseFilename).name for h in file_handlers] == ["b.yaml"]
    logger.removeHandler(foreign)


def test_get_logger_configures_default_file_on_first_use(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    note("hello")

    assert [e["message"] for e in load_log(tmp_path / DEFAULT_LOG_FILE)] == ["hello"]
    assert get_logger() is get_logger()
    assert len(lager_handlers()) == 1


def test_foreign_handler_does_not_prevent_default_configuration(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    logger = logging.getLogger(LOGGER_NAME)
    foreign = logging.NullHandler()
    logger.addHandler(foreign)

    note("still recorded")
    get_logger()

    assert [e["message"] for e in load_log(tmp_path / DEFAULT_LOG_FILE)] == [
        "still recorded"
    ]
    assert logger.level == logging.INFO
    assert foreign in logger.handlers
    assert len(lager_handlers()) == 1


def test_log_is_not_created_until_first_record(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "lazy.yaml"
    configure_lager(path)
    assert not path.exists()


def test_level_filters_function_records(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "log.yaml"
    configure_lager(path, level=logging.WARNING)

    @lager
    def quiet(x):
        return x

    @lager(level=logging.ERROR)
    def loud(x):
        return x

    quiet(1)
    loud(2)

    (entry,) = load_log(path)
    assert entry["function"] == "loud"


def test_custom_logger_receives_records(log_file: pathlib.Path) -> None:
    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    custom = logging.getLogger("lager.test.custom")
    custom.setLevel(logging.INFO)
    custom.propagate = False
    handler = Collector()
    custom.addHandler(handler)
    try:

        @lager(logger=custom)
        def f(x):
            return x

        f(1)
    finally:
        custom.removeHandler(handler)

    assert len(records) == 1
    assert records[0].lager["params"] == {"x": 1}  # type: ignore[attr-defined]
    assert not log_file.exists()


def test_load_log_of_empty_file_is_empty_list(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    assert load_log(path) == []


def test_load_log_missing_file_raises(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_log(tmp_path / "missing.yaml")


def test_plain_log_records_are_written_as_valid_yaml(
    log_file: pathlib.Path,
) -> None:
    logging.getLogger(LOGGER_NAME).warning("disk %s", "full")

    (entry,) = load_log(log_file)
    assert entry["level"] == "WARNING"
    assert entry["message"] == "disk full"
    assert set(entry) == {"timestamp", "level", "message"}


def test_log_file_is_a_single_yaml_list(log_file: pathlib.Path) -> None:
    @lager
    def f(x):
        return x

    f("multi\nline: text")
    f(2)

    loaded = yaml.safe_load(log_file.read_text(encoding="utf-8"))
    assert [e["params"]["x"] for e in loaded] == ["multi\nline: text", 2]


# --------------------------------------------------------------------------- #
# note
# --------------------------------------------------------------------------- #
def test_note_writes_message_and_data_alongside_calls(
    log_file: pathlib.Path,
) -> None:
    @lager
    def filter_set(data, min_val=100):
        return data

    note("File loaded")
    filter_set("data")
    note(
        "Job done",
        n_samples=9832,
        thresholds={"min_vals": (100, 200)},
        out=pathlib.Path("a/b"),
    )
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


def test_note_without_message_or_data_still_writes_entry(
    log_file: pathlib.Path,
) -> None:
    note()

    (entry,) = load_log(log_file)
    assert entry["kind"] == "note"
    assert "message" not in entry and "data" not in entry
