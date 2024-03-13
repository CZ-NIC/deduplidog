from ast import literal_eval
from collections import defaultdict
from dataclasses import _MISSING_TYPE, dataclass
from datetime import datetime
from functools import cache, cached_property
from pathlib import Path
from types import UnionType
from typing import Any, get_args

from PIL import ExifTags, Image
import imagehash
from textual.widgets import Checkbox, Input


@dataclass
class Field:
    """ Bridge between the values given in CLI, TUI and real needed values (str to int conversion etc). """
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


class keydefaultdict(defaultdict):
    def __missing__(self, key):
        self[key] = self.default_factory(key)
        return self[key]


@dataclass
class FileMetadata:
    file: Path
    _pil = None
    cleaned_count = 0
    "Not used, just for debugging: To determine whether the clean up is needed or not."

    @cached_property
    def exif_times(self):
        try:
            return {datetime.strptime(v, '%Y:%m:%d %H:%M:%S').timestamp()
                    for k, v in self.get_pil()._getexif().items()
                    if k in ExifTags.TAGS and "DateTime" in ExifTags.TAGS[k]}
        except:
            return tuple()

    @cached_property
    def average_hash(self):
        return imagehash.average_hash(self.get_pil())

    @cached_property
    def stat(self):
        return self.file.stat()

    def get_pil(self):
        if not self._pil:
            self._pil = Image.open(self.file)
        return self._pil

    def preload(self):
        """ Preload all values. """
        self.exif_times
        self.average_hash
        self.stat
        self.clean()  # PIL will never be needed anymore
        return True

    def clean(self):
        """ As PIL is the most memory consuming, we allow the easy clean up. """
        self._pil = None
        self.cleaned_count += 1
