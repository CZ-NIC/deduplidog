from ast import literal_eval
from dataclasses import _MISSING_TYPE, dataclass
from types import UnionType
from typing import Any, get_args

from textual.widgets import Checkbox, Input


@dataclass
class Field:
    name: str
    value: Any
    type: Any
    help: str = ""

    def __post_init__(self):
        if isinstance(self.value, _MISSING_TYPE):
            self.value = ""
        self.types = get_args(self.type) \
            if isinstance(self.type, UnionType) else (self.type, )
        "All possible types in a tuple. Ex 'int | str' -> (int, str)"

    def get_widgets(self):
        if self.type is bool:
            o = Checkbox(self.name, self.value)
        else:
            o = Input(str(self.value), placeholder=self.name)
        o._link = self
        return o

    def convert(self):
        """ Convert the self.value to the given self.type.
            The value might be in str due to CLI or TUI whereas the programs wants bool.
        """
        if self.value == "True":
            return True
        if self.value == "False":
            return False
        if type(self.value) is str and str not in self.types:
            try:
                return literal_eval(self.value)  # ex: int, tuple[int, int]
            except:
                raise ValueError(f"{self.name}: Cannot convert value {self.value}")
        return self.value
