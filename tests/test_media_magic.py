from unittest import main

from tests.setup import FolderState, drun, TestDisk


class TestMediaMagicTwoFolders(TestDisk):
    def setUp(self):
        super().setUp()
        self.state = FolderState(self, self.disk / "folder2", self.disk / "folder1")

    def test_basic(self):
        d = drun(["rename", "execute"], ["neglect_warning"], [], ["media_magic"], **self.state)
        self.state.check(prefixed=("dog1.jpg", "folder2.1/dog2.mp4"))

        self.log([{'folder1/dog1.jpg': [], 'folder2/dog1.jpg': ['renaming']},
                  {"folder2/folder2.1/dog2.mp4": ['SIZE WARNING 77.5 kB', 'renaming'], 'folder1/dog2.mp4': [], }], d)

    def test_ignore_name(self):
        d = drun(["rename", "execute"], ["neglect_warning"], ["ignore_name"], ["media_magic"], **self.state)
        self.state.check(prefixed=("dog1.jpg", "dog1_other_name.jpg", "folder2.1/dog2.mp4"))

    def test_ignore_name_small_hash(self):
        # dog2.jpg in the work dir is bigger than the one in the originals dir
        # the warning should be triggered
        # Note that it needs bigger hash difference to be detected.
        d = drun(["rename", "execute"], [], ["ignore_name"],
                 {"media_magic": True, "accepted_img_hash_diff": 3}, **self.state)
        self.log([{"folder2.1/dog2.jpg": ['SIZE WARNING 195.5 kB', 'renaming']}], d)
        self.state.check(prefixed=("dog1.jpg", "dog1_other_name.jpg"))

    def test_ignore_name_small_hash(self):
        drun(["rename", "execute"], ["neglect_warning"], ["ignore_name"],
             {"media_magic": True, "accepted_img_hash_diff": 3}, **self.state)
        self.state.check(prefixed=("dog1.jpg", "dog1_other_name.jpg", "folder2.1/dog2.jpg", "folder2.1/dog2.mp4"))


class TestMediaMagicSwapped(TestDisk):
    def setUp(self):
        super().setUp()
        self.state = FolderState(self, self.disk / "folder1", self.disk / "folder2")

    # TODO check on travis, locally, it works
    # def test_basic(self):
    #     d = drun(["rename", "execute"], ["neglect_warning"], [], ["media_magic"], **self.state)
    #     self.state.check(prefixed=("dog1.jpg", "dog2.mp4"))
    #     self.log([{"folder1/dog2.mp4": ["renaming"], "folder2/folder2.1/dog2.mp4": []},
    #               {"folder1/dog1.jpg": ['SIZE WARNING 75.2 kB', 'renaming'], "folder2/dog1.jpg": ['DATE WARNING + 29 seconds']}, ], d)

    #  NOTE add
    # def test_skip_bigger(self):
    #     state = self.prepare()
    #     Deduplidog(*state, rename=True, execute=True, ignore_date=True, skip_bigger=True, `media_magic=True`)
    #     state.check()


if __name__ == '__main__':
    main()
