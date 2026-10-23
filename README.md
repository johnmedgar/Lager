# Lager<sup>*</sup>
[Report a bug](https://github.com/stemcellbioengineering/Lager/issues)
Record the parameters your functions, methods and classes are called with, as key-value pairs in a YAML log.
Lager is built on Python's standard `logging` library and can be used as a decorator or as a wrapper.

*has nothing to do with [lager](https://en.wikipedia.org/wiki/Lager)-style beer.

## Installation
Clone the repository from https://github.com/stemcellbioengineering/Lager/. Then open your terminal, navigate to the root directory (contains the pyproject.toml file), and install the package and dependencies using:
```bash
pip install .
```
Optionally, install in editable mode `pip install -e .` for testing or development.

Requires Python 3.8+ and PyYAML (installed automatically).

## Usage

```python
from lager import lager, lager_module, configure, load_log

# Optional: choose the log file. Without this, calls are appended to ./lager.yaml
configure("params.yaml", mode="w")

# Decorate a function
@lager
def train(lr, epochs=10, **kwargs):
    ...

# Decorate a class to record its instantiation; keep secrets out of the log
@lager(exclude=["token"])
class Client:
    def __init__(self, url, token, timeout=5.0):
        ...

    @lager                      # record method calls too
    def get(self, path, retries=3):
        ...

# Wrap an existing function without decorator syntax
import math
logged_hypot = lager(math.hypot)

# Wrap every function and class defined in a module
import my_module
lager_module(my_module)

train(0.01, optimizer="adam")
client = Client("https://example.com", token="s3cret")
client.get("/status")
logged_hypot(3, 4)
my_module.some_function(42)

# Read the log back as a list of dicts
for entry in load_log("params.yaml"):
    print(entry["function"], entry["params"])
```

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

The file stays valid YAML when appended to across runs (`configure(..., mode="a")`).

## Options

`lager(...)` and `lager_module(...)` accept:

| Option             | Default         | Description                                          |
| ------------------ | --------------- | ---------------------------------------------------- |
| `exclude`          | `()`            | Parameter names never written to the log.            |
| `include_defaults` | `True`          | Record parameters left at their default values.      |
| `level`            | `logging.INFO`  | Logging level of the emitted records.                |
| `logger`           | `"lager"` logger | Send records to a different logger.                 |

`lager_module` also accepts `include_private=True` to wrap names starting with `_`.

## Notes

- `self` and `cls` are not recorded; `**kwargs` are flattened into the parameters.
- Values YAML can't represent directly are stored as text (paths, dates, bytes) or as their `repr()`.
- `lager_module` only affects calls made through the module (`my_module.func()`). Names imported with
  `from my_module import func` before wrapping stay unwrapped.
- If recording fails, the wrapped call still runs normally.

## License
This project is licensed under the GNU General Public License v3.0.