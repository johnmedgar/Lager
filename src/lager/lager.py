"""Lager: record the parameters passed to functions, methods and classes.

Every call to a wrapped callable is emitted through the standard ``logging``
library and written to a YAML file as one entry of a top-level list, so the
file can be appended to indefinitely and still be loaded with ``yaml.safe_load``.

Example log entry::

    - timestamp: '2026-10-23T08:15:00.123456'
      kind: function
      function: train
      qualname: train
      module: __main__
      params:
        lr: 0.001
        epochs: 10

Usage::

    from lager import lager, configure_lager

    configure_lager("params.yaml")          # optional, defaults to ./lager.yaml

    @lager
    def train(lr, epochs=10): ...

    @lager(exclude=["password"])
    class Client:
        def __init__(self, user, password): ...

    fast_fft = lager(numpy.fft.fft)   # wrap without decorator syntax
"""

from __future__ import annotations

import datetime as _dt
import functools
import inspect
import logging
import os
import types
from collections.abc import Iterable, Mapping
from typing import Any, Callable, Optional, TypeVar, Union, overload

import yaml

__all__ = [
    "LOGGER_NAME",
    "DEFAULT_LOG_FILE",
    "YamlFormatter",
    "configure_lager",
    "get_logger",
    "lager",
    "load_log",
    "note",
]

LOGGER_NAME = "lager"
DEFAULT_LOG_FILE = "lager.yaml"

_MAX_DEPTH = 20

F = TypeVar("F", bound=Callable[..., Any])


# --------------------------------------------------------------------------- #
# Logging setup
# --------------------------------------------------------------------------- #
class YamlFormatter(logging.Formatter):
    """Format log records as single-item YAML lists.

    Records produced by :func:`lager` carry their payload in ``record.lager``.
    Any other record sent to the logger is still written as valid YAML.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry = getattr(record, "lager", None)
        if entry is None:
            entry = {
                "timestamp": _dt.datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S'), #.isoformat(),
                "level": record.levelname,
                "message": record.getMessage(),
            }
        # Dumping as a one-element list makes concatenated entries a valid YAML list.
        return yaml.safe_dump(
            [entry],
            sort_keys=False,
            default_flow_style=None, #False,
            allow_unicode=True,
            indent=2
        )#.rstrip("\n")


def configure_lager(
    log_file: Union[str, os.PathLike] = DEFAULT_LOG_FILE,
    mode: str = "w",
    level: int = logging.INFO,
    propagate: bool = False,
) -> logging.Logger:
    """Configure where lager writes its YAML log.

    Replaces any handlers previously installed by lager.

    Args:
        log_file: Path of the YAML file to write (default: 'lager.yaml').
        mode: ``"a"`` to append to an existing log, ``"w"`` to overwrite it (default: ``"w"``).
        level: Logging level for the lager logger (default: ``logging.INFO``).
        propagate: Whether records also propagate to ancestor loggers (default: False).

    Returns:
        The configured ``logging.Logger``.
    """
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if getattr(handler, "_lager_handler", False):
            logger.removeHandler(handler)
            handler.close()

    handler = logging.FileHandler(log_file, mode=mode, encoding="utf-8", delay=True)
    handler.setFormatter(YamlFormatter())
    handler._lager_handler = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = propagate
    return logger


def get_logger() -> logging.Logger:
    """Return the lager logger, configuring the default file on first use."""
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        configure_lager()
    return logger


def note(
    message: Optional[str] = None,
    /,
    *,
    level: int = logging.INFO,
    logger: Optional[logging.Logger] = None,
    **data: Any,
) -> None:
    """Write a free-form entry, with optional structured values, to the lager log.

    Example::

        note("QC done", n_cells=9832, thresholds={"min_genes": 100})

    Args:
        message: Optional text describing the entry.
        level: Logging level of the emitted record.
        logger: Logger to use; defaults to the ``"lager"`` logger.
        **data: Values recorded as key-value pairs under ``data``.
    """
    logger = logger or get_logger()
    if not logger.isEnabledFor(level):
        return
    entry: dict[str, Any] = {
        "timestamp": _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), #.isoformat(),
        "kind": "note",
        "level": logging.getLevelName(level),
    }
    if message is not None:
        entry["message"] = str(message)
    if data:
        entry["data"] = _to_yaml_safe(data)
    logger.log(level, "%s", message or "note", extra={"lager": entry})


def load_log(log_file: Union[str, os.PathLike] = DEFAULT_LOG_FILE) -> list[dict]:
    """Load a lager YAML log back into a list of entries."""
    with open(log_file, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


# --------------------------------------------------------------------------- #
# Parameter capture
# --------------------------------------------------------------------------- #
def _to_yaml_safe(value: Any, depth: int = 0) -> Any:
    """Convert ``value`` into something ``yaml.safe_dump`` can represent."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth >= _MAX_DEPTH:
        return repr(value)
    if isinstance(value, Mapping):
        return {
            (k if isinstance(k, (str, int, float, bool)) else repr(k)): _to_yaml_safe(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_yaml_safe(v, depth + 1) for v in value]
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, (_dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="backslashreplace")
    return repr(value)


def _signature(func: Callable) -> Optional[inspect.Signature]:
    try:
        return inspect.signature(func)
    except (TypeError, ValueError):  # some builtins have no signature
        return None


def _bind_params(
    sig: Optional[inspect.Signature],
    args: tuple,
    kwargs: dict,
    include_defaults: bool,
    skip_first: bool,
) -> dict:
    """Map call arguments to parameter names as a flat key/value dict."""
    if sig is None:
        params: dict = {f"arg{i}": a for i, a in enumerate(args)}
        params.update(kwargs)
        return params

    try:
        bound = sig.bind(*args, **kwargs)
    except TypeError:
        # The call itself is invalid; record what we got and let it raise.
        params = {f"arg{i}": a for i, a in enumerate(args)}
        params.update(kwargs)
        return params
    if include_defaults:
        bound.apply_defaults()

    params = {}
    for i, (name, value) in enumerate(bound.arguments.items()):
        if i == 0 and skip_first:
            continue
        kind = sig.parameters[name].kind
        if kind is inspect.Parameter.VAR_KEYWORD:
            params.update(value)  # flatten **kwargs into the key/value pairs
        elif kind is inspect.Parameter.VAR_POSITIONAL:
            if value:
                params[name] = list(value)
        else:
            params[name] = value
    return params


def _is_method_like(func: Callable) -> bool:
    """True if ``func`` looks like a plain function defined in a class body."""
    sig = _signature(func)
    if sig is None or "." not in getattr(func, "__qualname__", ""):
        return False
    first = next(iter(sig.parameters), None)
    return first in ("self", "cls")


def _emit(
    logger: logging.Logger,
    level: int,
    kind: str,
    target: Any,
    params: dict,
    exclude: frozenset,
) -> None:
    if not logger.isEnabledFor(level):
        return
    name = getattr(target, "__name__", repr(target))
    entry = {
        "timestamp": _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), #.isoformat(),
        "kind": kind,
        "function": name,
        "qualname": getattr(target, "__qualname__", name),
        "module": getattr(target, "__module__", None),
        "params": {
            k: _to_yaml_safe(v) for k, v in params.items() if k not in exclude
        },
    }
    logger.log(level, "%s %s called", kind, entry["qualname"], extra={"lager": entry})


def _wrap_function(
    func: F,
    *,
    kind: str,
    record_target: Any,
    logger: Optional[logging.Logger],
    level: int,
    include_defaults: bool,
    exclude: frozenset,
    skip_first: bool,
) -> F:
    sig = _signature(func)

    def record(args: tuple, kwargs: dict) -> None:
        try:
            params = _bind_params(sig, args, kwargs, include_defaults, skip_first)
            _emit(logger or get_logger(), level, kind, record_target, params, exclude)
        except Exception:  # logging must never break the wrapped call
            logging.getLogger(LOGGER_NAME).debug("lager failed to record call", exc_info=True)

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            record(args, kwargs)
            return await func(*args, **kwargs)

        wrapper = async_wrapper
    else:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            record(args, kwargs)
            return func(*args, **kwargs)

    wrapper.__lager_wrapped__ = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]


def _wrap_class(cls: type, **opts: Any) -> type:
    if getattr(cls.__dict__.get("__init__"), "__lager_wrapped__", False):
        return cls

    init: Callable[..., None] = cls.__init__
    if init is object.__init__:
        # No custom __init__: describe parameters using the class signature
        # (covers __new__-based constructors).
        sig = _signature(cls)

        def init(self):
            object.__init__(self)
        # def init(self, *args, **kwargs):
        #     object.__init__(self)

        if sig is not None:
            self_param = inspect.Parameter("self", inspect.Parameter.POSITIONAL_ONLY)
            init.__signature__ = sig.replace(  # type: ignore[attr-defined]
                parameters=[self_param, *sig.parameters.values()]
            )
        init.__qualname__ = f"{cls.__qualname__}.__init__"

    setattr(
        cls,
        "__init__",
        _wrap_function(init, kind="class", record_target=cls, skip_first=True, **opts),
    )
    return cls


@overload
def lager(target: F) -> F: ...
@overload
def lager(
    target: None = None,
    *,
    logger: Optional[logging.Logger] = None,
    level: int = logging.INFO,
    include_defaults: bool = True,
    exclude: Iterable[str] = (),
) -> Callable[[F], F]: ...


def lager(
    target: Any = None,
    *,
    logger: Optional[logging.Logger] = None,
    level: int = logging.INFO,
    include_defaults: bool = True,
    exclude: Iterable[str] = (),
):
    """Record the parameters a function, method or class is called with.

    Can be used as a decorator ``@lager`` or a wrapper ``lager(func)``.

    Args:
        target: The function, method, or class to wrap.
        logger: Logger to use; defaults to the ``"lager"`` logger.
        level: Logging level of the emitted records.
        include_defaults: Also record parameters left at their default value.
        exclude: Parameter names never written to the log (e.g. secrets).

    Returns:
        The wrapped callable (classes are patched in place and returned),
        or a decorator if ``target`` is omitted.
    """
    opts: dict[str, Any] = {
        "logger": logger,
        "level": level,
        "include_defaults": include_defaults,
        "exclude": frozenset(exclude),
    }

    def decorate(obj: Any) -> Any:
        if inspect.isclass(obj):
            return _wrap_class(obj, **opts)
        if isinstance(obj, (staticmethod, classmethod)):
            inner = obj.__func__
            wrapped = _wrap_function(
                inner,
                kind="method",
                record_target=inner,
                skip_first=isinstance(obj, classmethod),
                **opts,
            )
            return type(obj)(wrapped)
        if callable(obj):
            method = _is_method_like(obj)
            return _wrap_function(
                obj,
                kind="method" if method else "function",
                record_target=obj,
                skip_first=method,
                **opts,
            )
        raise TypeError(f"lager cannot wrap object of type {type(obj).__name__}")

    if target is None:
        return decorate
    return decorate(target)
