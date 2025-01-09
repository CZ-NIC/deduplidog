
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import Self
from unittest import TestCase, main

from tests.setup import FileRepresentation, FolderState, drun


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
        state.check(prefixed_i=(11,))

    def test_date(self):
        state = self.prepare()
        drun(["rename", "execute"], ["neglect_warning"], ["ignore_date"], **state)
        state.check(prefixed_i=(4, 5, 6, 7, 8, 9, 10, 11))
        state = self.prepare()
        drun(["rename", "execute"], match=["ignore_date"], **state)
        state.check(prefixed_i=(4, 5, 6, 7, 11))

        state = self.prepare()
        drun(["rename", "execute"], ["neglect_warning"], {"tolerate_hour": 1}, **state)
        state.check(prefixed_i=(4, 7, 8, 9, 11))
        state = self.prepare()
        drun(["rename", "execute"], match={"tolerate_hour": 1}, **state)
        state.check(prefixed_i=(4, 7, 11))

        state = self.prepare()
        drun(["rename", "execute"], ["neglect_warning"], {"tolerate_hour": 2},  **state)
        state.check(prefixed_i=(4, 5, 6, 7, 8, 9, 11))
        state = self.prepare()
        drun(["rename", "execute"], match={"tolerate_hour": 2}, **state)
        state.check(prefixed_i=(4, 5, 6, 7, 11))

    def test_replace_with_original(self):
        state = self.prepare()
        drun(["replace_with_original", "execute"], ["neglect_warning"], **state)
        state.work_files["file_11"].suck(state.originals["file_11"])
        state.check()

        state = self.prepare()
        drun(["replace_with_original", "execute"], ["neglect_warning"], {"tolerate_hour": 2}, **state)
        state.check(suck_i=(4, 5, 6, 7, 8, 9, 11))

    def test_invert_selection(self):
        state = self.prepare()
        with self.assertRaises(AssertionError):
            drun(["replace_with_original", "execute"], match={"tolerate_hour": 2, "invert_selection": True}, **state)
        drun(["rename", "execute"], ["neglect_warning"], {"tolerate_hour": 2, "invert_selection": False}, **state)
        state.check(prefixed_i=(4, 5, 6, 7, 8, 9, 11))

        state = self.prepare()
        drun(["rename", "execute"], ["neglect_warning"], {"tolerate_hour": 2, "invert_selection": True}, **state)
        state.check(prefixed_i=(1, 2, 10))


if __name__ == '__main__':
    main()
