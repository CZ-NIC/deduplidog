
from pathlib import Path
from tests.setup import FolderState, TestDisk, drun


class TestSymlinked(TestDisk):
    def setUp(self):
        super().setUp()

    def test_basic(self):
        state = FolderState(self, self.disk / "folder1", self.disk / "folder2")
        # We have to ignore the date as the remote checkout resets the mtime
        d = drun(["rename", "execute"], [], ["ignore_date"], [], **state)
        state.check(prefixed=("2.txt", "1.txt"))
        self.log([
            {"folder1/2.txt": ["renaming"], "folder2/2.txt": []},
            {"folder1/1.txt": ["renaming"], "folder2/folder2.1/1.txt": []},
        ], d)

    def test_reverse(self):
        state = FolderState(self, self.disk / "folder2", self.disk / "folder1")
        d = drun(["rename", "execute"], [], ["ignore_date"], [], **state)
        state.check(prefixed=("folder2.1/1.txt", "2.txt"))
        self.log([
            {"folder2/2.txt": ["renaming"], "folder1/2.txt": []},
            {"folder2/folder2.1/1.txt": ["renaming"], "folder1/1.txt": []},
        ], d)
