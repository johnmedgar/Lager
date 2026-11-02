# Lager<sup>*</sup>
[![Python application](https://github.com/stemcellbioengineering/Lager/actions/workflows/python-app.yml/badge.svg)](https://github.com/stemcellbioengineering/Lager/actions/workflows/python-app.yml)

Record the parameters your functions, methods and classes are called with, as key-value pairs in a YAML log. Lager is built on Python's standard `logging` library and can be used as a decorator or as a wrapper.

*has nothing to do with [lager-style beer](https://en.wikipedia.org/wiki/Lager).

## Installation
[Clone the Lager repository](https://github.com/stemcellbioengineering/Lager/) then use the terminal to navigate to the root directory (containing the pyproject.toml file). Install the package and dependencies using:
```bash
pip install .
```
Optionally, install in editable mode `pip install -e .` for testing or development.

### Dependencies
Requires Python >3.8 and PyYAML (installed automatically).

## Usage

For logging parameters in functions, methods, or classes:

```python
from lager import lager, configure_lager, load_log

# Optional: provide a name for the log file. Otherwise, calls are written to ./lager.yaml
configure_lager("params.yaml")


# Decorate a function
@lager
def train(lr, epochs=10, **kwargs):
    ...

# Decorate a class to record its instantiation; keep secrets out of the log
@lager(exclude=["token"])
class Client:
    def __init__(self, url, token, timeout=5.0):
        ...
        
    # record method calls too
    @lager
    def get(self, path, retries=3):
        ...

# Wrap an existing function without the decorator syntax
import math
math_hypot = lager(math.hypot)
result = math_hypot(1,4,6)   # call math_hypot exactly like math.hypot

# This works too
import math
result = lager(math.hypot)(1,4,6)


# Read the log back as a list of dicts
for entry in load_log("params.yaml"):
    print(entry["function"], entry["params"])
```

Lager can also wrap every function and class defined in a module:

```python
import my_module
from lager import lagmod

lagmod(my_module)
my_module.func(...)   # call functions or classes within the module
```

### Logging notes and other values

Use `note` to write a message and/or extra values to the same log:

```python
import logging
from lager import note

note("Loaded sample")
note("QC done", n_cells=9832, thresholds={"min_genes": 100})
```

Keyword values are stored as key-value pairs under `data`:

```yaml
- timestamp: '2026-10-24T09:12:03.513402'
  kind: note
  level: INFO
  message: QC done
  data:
    n_cells: 9832
    thresholds:
      min_genes: 100
```

`note` accepts `level` (default `logging.INFO`; notes below the configured level are dropped) and `logger`, so these two names can't be used as data keys. `message` and `data` are omitted when not given.

## Log format

Each call adds one entry to a YAML list:

```yaml
- timestamp: '2026-10-23T08:26:20.095749'
  kind: function
  function: train
  qualname: train
  module: __main__
  params:
    lr: 0.01
    epochs: 10
    optimizer: adam
- timestamp: '2026-10-23T08:26:20.096248'
  kind: class
  function: Client
  qualname: Client
  module: __main__
  params:
    url: https://example.com
    timeout: 5.0
```

By default, a new YAML is created each time a script is run, overwriting any existing (`mode="w"`). The YAML file can also be appended to by setting the mode in `configure_lager(..., mode="a")`. This is useful when re-running scripts with different parameters to keep track of the parameters in each run.

## Options

`lager(...)` and `lagmod(...)` accept:

| Option             | Default         | Description                                          |
| ------------------ | --------------- | ---------------------------------------------------- |
| `exclude`          | `()`            | Parameter names never written to the log.            |
| `include_defaults` | `True`          | Record parameters left at their default values.      |
| `level`            | `logging.INFO`  | Logging level of the emitted records.                |
| `logger`           | `"lager"`       | Send records to a different logger.                 |

`lagmod` also accepts `include_private=True` to wrap names starting with `_`.

## Notes

- `self` and `cls` are not recorded; `**kwargs` are flattened into the parameters.
- Values YAML can't represent directly are stored as text (paths, dates, bytes) or as their `repr()`.
- `lagmod` only affects calls made through the module (`my_module.func()`). Names imported with
  `from my_module import func` before wrapping stay unwrapped.
- If recording fails, the wrapped call still runs normally.

## License
This project is licensed under the GNU General Public License v3.0.