from ast import literal_eval
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from os import stat_result
from pathlib import Path
from typing import Literal, Self

from PIL import ExifTags, Image
from imagehash import ImageHash, average_hash


class keydefaultdict(defaultdict):
    def __missing__(self, key):
        self[key] = self.default_factory(key)
        return self[key]


@dataclass
class FileMetadata:
    file: Path
    _exif_times: set | tuple | None = None
    _average_hash: ImageHash | None | Literal[False] = None
    _stat: stat_result | None = None
    _pil = None
    cleaned_count = 0
    "Not used, just for debugging: To determine whether the clean up is needed or not."
    max_size: int = 0
    """ If file is bigger than this bytes, do not count hash. """

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
            if self.max_size and self.stat.st_size > self.max_size:
                self._average_hash = False
            else:
                try:
                    self._average_hash = average_hash(self.get_pil())
                except OSError:  # computing failed, put a hash that means no img is comparable
                    self._average_hash = False
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
    def preload(cls, file, max_size=None) -> Self | None:
        """ Preload all values. """
        o = cls(file, max_size=max_size)
        o.exif_times, o.average_hash, o.stat
        o.clean()  # PIL will never be needed anymore
        return o

    def clean(self):
        """ As PIL is the most memory consuming, we allow the easy clean up. """
        self._pil = None
        self.cleaned_count += 1
