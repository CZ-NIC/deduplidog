
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import chain
import os
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import Self
from unittest import TestCase, main
import random
import string

from deduplidog import Deduplidog
from deduplidog.deduplidog import Action, Execution, Match, Media, Helper


@dataclass
class FileRepresentation:
    path: Path
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
        "Checks the disk whether it contains the file represented."
        test.assertTrue(self.path.exists(), msg=self.path)
        test.assertEqual(self.get_text(), self.path.read_text(), msg=self.path)
        test.assertEqual(self._mtime, self.path.stat().st_mtime, msg=self.path)

    def get_text(self):
        random.seed(self.text_seed)
        return ''.join(random.choices(string.ascii_letters + string.digits, k=10+self.text_seed*10))

    def prefixed(self):
        self.path = self.path.with_name("✓" + self.path.name)

    def suck(self, other: Self):
        "Use the other file. Use its name, however stays in the current directory."
        self.path = self.path.with_name(other.path.name)
        self._mtime = other._mtime
        self.text_seed = other.text_seed


@dataclass
class FolderState(Mapping):
    test_case: TestCase
    _work_dir: Path
    _original_dir: Path
    work_files: dict[str, FileRepresentation]
    originals: dict[str, FileRepresentation]

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

    def check(self, prefixed: tuple[int] = None, suck: tuple[int] = None):
        """Checks the file changes

        :param prefixed: These files in the work dir are expected to be prefixed
        :param suck: These files in the work dir are expected to be sucked from the originals
        """
        [self.work_files[f"file_{i}"].prefixed() for i in prefixed or ()]
        [self.work_files[f"file_{i}"].suck(self.originals[f"file_{i}"]) for i in suck or ()]
        [f.check(self.test_case) for f in chain(self.work_files.values(), self.originals.values())]


def drun(action=None, execution=None, match=None, media=None, helper=None, **kw):
    def _(l: list | dict):
        if isinstance(l, list):
            return {k: True for k in l}
        return l
    return Deduplidog(Action(**_(action or [])),
                      Execution(**_(execution or [])),
                      Match(**_(match or [])),
                      Media(**_(media or [])),
                      Helper(**_(helper or [])),
                      **kw).start()


class TestDeduplidog(TestCase):

    def prepare(self, testing_dir: str = None):
        self.temp = mkdtemp()  # TemporaryDirectory() NOTE
        # temp = Path(testing_dir) if testing_dir else self.temp.name NOTE
        temp = str(self.temp)
        originals = Path(temp, "originals")
        work_dir = Path(temp, "work_dir")
        if not testing_dir:
            originals.mkdir()
            work_dir.mkdir()

        original_files = {name: FileRepresentation(originals / name).write()
                          for name in (f"file_{i}" for i in range(12))}
        work_files = {name: FileRepresentation(work_dir / name, *rest).write() for name, *rest in (
            ("file_1", 0, 2),
            ("file_2", 0, 3),
            ("file_4", 3600),
            ("file_5", 7200),
            ("file_6", 3601),
            ("file_7", 3599),
            ("file_8", -3600),
            ("file_9", -10),
            ("file_10", -3600*24*365),
            ("file_11", 0),
        )}

        return FolderState(self, work_dir, originals, work_files, original_files)

    def test_simple_prefix(self):
        state = self.prepare()
        drun(["rename", "execute"], **state)
        state.check(prefixed=(11,))

    def test_date(self):
        state = self.prepare()
        drun(["rename", "execute"], ["neglect_warning"], ["ignore_date"], **state)
        state.check(prefixed=(4, 5, 6, 7, 8, 9, 10, 11))
        state = self.prepare()
        drun(["rename", "execute"], match=["ignore_date"], **state)
        state.check(prefixed=(4, 5, 6, 7, 11))

        state = self.prepare()
        drun(["rename", "execute"], ["neglect_warning"], {"tolerate_hour": 1}, **state)
        state.check(prefixed=(4, 7, 8, 9, 11))
        state = self.prepare()
        drun(["rename", "execute"], match={"tolerate_hour": 1}, **state)
        state.check(prefixed=(4, 7, 11))

        state = self.prepare()
        drun(["rename", "execute"], ["neglect_warning"], {"tolerate_hour": 2},  **state)
        state.check(prefixed=(4, 5, 6, 7, 8, 9, 11))
        state = self.prepare()
        drun(["rename", "execute"], match={"tolerate_hour": 2}, **state)
        state.check(prefixed=(4, 5, 6, 7, 11))

    def test_replace_with_original(self):
        state = self.prepare()
        drun(["replace_with_original", "execute"], ["neglect_warning"], **state)
        state.work_files["file_11"].suck(state.originals["file_11"])
        state.check()

        state = self.prepare()
        drun(["replace_with_original", "execute"], ["neglect_warning"], {"tolerate_hour": 2}, **state)
        state.check(suck=(4, 5, 6, 7, 8, 9, 11))

    def test_invert_selection(self):
        state = self.prepare()
        with self.assertRaises(AssertionError):
            drun(["replace_with_original", "execute"], match={"tolerate_hour": 2, "invert_selection": True}, **state)
        drun(["rename", "execute"], ["neglect_warning"], {"tolerate_hour": 2, "invert_selection": False}, **state)
        state.check(prefixed=(4, 5, 6, 7, 8, 9, 11))

        state = self.prepare()
        drun(["rename", "execute"], ["neglect_warning"], {"tolerate_hour": 2, "invert_selection": True}, **state)
        state.check(prefixed=(1, 2, 10))

    #  No media file in the test case.
    # def test_skip_bigger(self):
    #     state = self.prepare()
    #     Deduplidog(*state, rename=True, execute=True, ignore_date=True, skip_bigger=True, `media_magic=True`)
    #     state.check()


if __name__ == '__main__':
    main()
