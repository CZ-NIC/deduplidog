from ast import literal_eval
from collections import defaultdict
from dataclasses import _MISSING_TYPE, dataclass
from datetime import datetime
from functools import cache
from os import stat_result
from pathlib import Path
from types import UnionType
from typing import Any, get_args

from PIL import ExifTags, Image
from imagehash import ImageHash, average_hash
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
    _exif_times: set | tuple | None = None
    _average_hash: ImageHash | None = None
    _stat: stat_result | None = None
    _pil = None
    cleaned_count = 0
    "Not used, just for debugging: To determine whether the clean up is needed or not."

    @property
    def exif_times(self):
        if not self._exif_times:
            try:
                self._exif_times = {datetime.strptime(v, '%Y:%m:%d %H:%M:%S').timestamp()
                                    for k, v in self.get_pil()._getexif().items()
                                    if k in ExifTags.TAGS and "DateTime" in ExifTags.TAGS[k]}
            except:
                self._exif_times = tuple()
        return self._exif_times

    @property
    def average_hash(self):
        if not self._average_hash:
            self._average_hash = average_hash(self.get_pil())
        return self._average_hash

    @property
    def stat(self):
        if not self._stat:
            self._stat = self.file.stat()
        return self._stat

    def get_pil(self):
        if not self._pil:
            self._pil = Image.open(self.file)
        return self._pil

    @classmethod
    def preload(cls, file):
        """ Preload all values. """
        o = cls(file)
        r = file, o.exif_times, o.average_hash, o.stat
        o.clean()  # PIL will never be needed anymore
        return r

    def clean(self):
        """ As PIL is the most memory consuming, we allow the easy clean up. """
        self._pil = None
        self.cleaned_count += 1
