import os
import random
import string
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from shutil import copytree, copy2
from tempfile import mkdtemp
from typing import Self
from unittest import TestCase

from deduplidog import Deduplidog
from deduplidog.deduplidog import Change, Action, Execution, Helper, Match, Media


def drun(action=None, execution=None, match=None, media=None, helper=None, confirm_one_by_one=False, **kw):
    def _(d: list | dict):
        if isinstance(d, list):
            return {k: True for k in d}
        return d

    # as confirm_one_by_one affects the testing, this option is lifted up here
    exec = {"confirm_one_by_one": confirm_one_by_one} if confirm_one_by_one is not None else {}

    return Deduplidog(Action(**_(action or [])),
                      Execution(**_(execution or {}) | exec),
                      Match(**_(match or [])),
                      Media(**_(media or [])),
                      Helper(**_(helper or [])),
                      **kw).start()


@dataclass
class FileReal:
    path: Path

    def __post_init__(self):
        self._mtime = self.path.stat().st_mtime

    def check(self, test: TestCase):
        "Checks the disk whether it contains the file represented."
        test.assertTrue(self.path.exists(), msg=f"This file should exist: {self.path}")
        test.assertEqual(self._mtime, self.path.stat().st_mtime, msg=self.path)

    def prefixed(self):
        self.path = self.path.with_name("✓" + self.path.name)

    def suck(self, other: Self):
        "Use the other file. Use its name, however stays in the current directory."
        self.path = self.path.with_name(other.path.name)
        self._mtime = other._mtime


@dataclass
class FileRepresentation(FileReal):
    # path: Path

    mtime: int = 0
    "relative mtime"
    text_seed: int = 1

    def __post_init__(self):
        self._mtime = round(self.path.parent.parent.stat().st_mtime + self.mtime)

    def write(self):
        "Writes the representation to the disk."
        self.path.write_text(self.get_text())
        os.utime(self.path, (self._mtime,)*2)
        return self

    def check(self, test: TestCase):
        super().check(test)
        if self.path.suffix not in (".jpeg",):
            test.assertEqual(self.get_text(), self.path.read_text(), msg=self.path)

    def get_text(self):
        random.seed(self.text_seed)
        return ''.join(random.choices(string.ascii_letters + string.digits, k=10+self.text_seed*10))

    def suck(self, other: Self):
        super().suck(other)
        self.text_seed = other.text_seed


@dataclass
class FolderState(Mapping):
    test_case: TestCase
    _work_dir: Path
    _original_dir: Path
    work_files: dict[str, FileReal] = field(default_factory=lambda: {})
    originals: dict[str, FileReal] = field(default_factory=lambda: {})

    def __post_init__(self):
        def _(dir_: Path, files_: dict):
            for file in dir_.rglob('*'):
                if file.is_file():
                    files_[str(file)] = FileReal(path=file)

        if not self.work_files:
            _(self._work_dir, self.work_files)
        if not self.originals:
            _(self._original_dir, self.originals)

    def __iter__(self):
        yield from ('work_dir', 'original_dir')

    def __len__(self):
        return 2

    def __getitem__(self, key):
        if key == 'work_dir':
            return self._work_dir
        elif key == 'original_dir':
            return self._original_dir
        else:
            raise KeyError(key)

    def check(self, prefixed: tuple[str] = None, suck: tuple[str] = None, prefixed_i: tuple[int] = None, suck_i: tuple[int] = None):
        """Checks the file changes

        :param prefixed: These files in the work dir are expected to be prefixed
        :param suck: These files in the work dir are expected to be sucked from the originals
        :param prefixed_i: These file_{i} in the work dir are expected to be prefixed
        :param suck_i: These file_{i} in the work dir are expected to be sucked from the originals
        """
        [self.work_files[str(self._work_dir / f)].prefixed() for f in prefixed or ()]
        [self.work_files[str(self._work_dir / f)].suck(self.originals[str(self._original_dir / f)]) for f in suck or ()]

        [self.work_files[f"file_{i}"].prefixed() for i in prefixed_i or ()]
        [self.work_files[f"file_{i}"].suck(self.originals[f"file_{i}"]) for i in suck_i or ()]

        [f.check(self.test_case) for f in chain(self.work_files.values(), self.originals.values())]


class TestDisk(TestCase):

    def setUp(self):
        self.disk2 = mkdtemp(dir="/tmp")
        temp = str(self.disk2)
        self.disk = Path(self.disk2) / "disk"
        copytree("tests/test_data/disk", self.disk, copy_function=copy2)

        # assure a file 29 seconds ahead (because timestamp seems reset when uploading on a testing worker)
        os.utime(self.disk / "folder1/dog1.jpg", (os.path.getmtime(self.disk / "folder2/dog1.jpg") - 29, ) * 2)

        # make a symlink
        os.symlink(self.disk / "folder1/symlinkable.txt", self.disk / "folder2/symlinkable.txt")

    def log(self, log: list[Change], deduplidog: Deduplidog):
        """ Check the deduplidog log output """
        # update the paths
        for row, change in zip(log, deduplidog.changes):
            self.assertDictEqual({self.disk / path: changes for path, changes in row.items()}, change)
