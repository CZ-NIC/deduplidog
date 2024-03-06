from dataclasses import dataclass
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime
from functools import cache
from itertools import chain
from pathlib import Path
import shutil
from time import sleep

import cv2
import imagehash
from humanize import naturaldelta, naturalsize
from IPython.display import Image, clear_output, display
from ipywidgets import HBox, widgets
from PIL import ExifTags, Image
from sh import find
from tqdm.notebook import tqdm


VIDEO_SUFFIXES = ".mp4", ".mov", ".avi", ".vob", ".mts", ".3gp", ".mpg", ".mpeg", ".wmv"
IMAGE_SUFFIXES = ".jpg", ".jpeg", ".png", ".gif"
MEDIA_SUFFIXES = IMAGE_SUFFIXES + VIDEO_SUFFIXES

logger = logging.getLogger(__name__)


@dataclass
class Deduplidog:
    """
    Find the duplicates.

    Normally, the file must have the same size, date and name. (Name might be just similar if parameters like strip_end_counter are set.)

    If media_magic=True, media files receive different rules: Neither the size nor the date are compared. See its help.
    """

    work_dir: str | Path
    "Folder of the files suspectible to be duplicates."
    original_dir: str | Path
    "Folder of the original files. Normally, these files will not be affected." \
        " (However, they might get affected by treat_bigger_as_original or set_both_to_older_date)."

    execute: bool = False
    "If False, nothing happens, just a safe run is performed."
    bashify: bool = False
    """Print bash commands that correspond to the actions that would have been executed if execute were True.
     You can check and run them yourself."""
    affect_only_if_smaller: bool = False
    """If media_magic=True, all writing actions like rename, replace_with_original, set_both_to_older_date and treat_bigger_as_original
     are executed only if the affectable file is smaller than the other."""
    rename: bool = False
    """If execute=True, prepend ✓ to the duplicated work file name (or possibly to the original file name if treat_bigger_as_original).
     Mutually exclusive with replace_with_original."""
    replace_with_original: bool = False
    """If execute=True, replace duplicated work file with the original (or possibly vice versa if treat_bigger_as_original).
    Mutually exclusive with rename.
    """
    set_both_to_older_date: bool = False
    "If execute=True, media_magic=True or (media_magic=False and ignore_date=True), both files are set to the older date. Ex: work file get's the original file's date or vice versa."
    treat_bigger_as_original: bool = False
    "If execute=True and rename=True and media_magic=True, the original file might be affected (by renaming) if smaller than the work file."

    casefold: bool = False
    "Case insensitive file name comparing."
    tolerate_hour: int | tuple[int, int] | bool = False
    """When comparing files in work_dir and media_magic=False, tolerate hour difference.
        Sometimes when dealing with FS changes, files might got shifted few hours.
        * bool → -1 .. +1
        * int → -int .. +int
        * tuple → int1 .. int2
        Ex: tolerate_hour=2 → work_file.st_mtime -7200 ... + 7200 is compared to the original_file.st_mtime """
    ignore_date: bool = False
    "If media_magic=False, files will not be compared by date."
    space2char: bool | str = False
    """When comparing files in work_dir, consider space as another char. Ex: "file 012.jpg" is compared as "file_012.jpg" """
    strip_end_counter: bool = False
    """When comparing files in work_dir, strip the counter. Ex: "00034(3).MTS" is compared as "00034.MTS" """
    strip_suffix: str = False
    """When comparing files in work_dir, strip the file name end matched by a regular. Ex: "001-edited.jpg" is compared as "001.jpg" """
    work_file_stem_shortened: int = None
    "Photos downloaded from Google have its stem shortened to 47 chars. For the comparing purpose, treat original folder file names shortened."

    media_magic: bool = False
    """
    Nor the size or date is compared for files with media suffixes.
    A video is considered a duplicate if it has the same name and a similar number of frames, even if it has a different extension.
    An image is considered a duplicate if it has the same name and a similar image hash, even if the files are of different sizes.
    """
    accepted_frame_delta: int = 1
    "Used only when media_magic is True"
    accepted_img_hash_diff: int = 1
    "Used only when media_magic is True"
    img_compare_date = False
    "If True and media_magic=True, aby se obrázek považoval za duplikát, musí mít podobný čas v EXIFu či souboru. Used only when media_magic is True."

    file_list: list[Path] = None
    "Use original file list. If none, a new is generated or a cached version is used."
    suffixes: bool | tuple[str] = False
    "If set, only files with such suffixes are compared. Ex: `suffixes = MEDIA_SUFFIXES`"

    skip: int = 0
    "Skip first n files in work_dir. Useful when a big task is interrupted and we want to continue without checking again the first part that has already been processed."

    debug: bool = None
    fail_on_error: bool = False
    shorter_log: bool = True
    "If True, common prefix of the file names are not output to the log to save space."
    logging_level: int = logging.WARNING

    ending_counter = re.compile(r"\(\d+\)$")

    def __post_init__(self):
        logging.basicConfig(level=self.logging_level, format="%(message)s", force=True)
        logger.setLevel(self.logging_level)
        [handler.setLevel(self.logging_level) for handler in logger.handlers]

        self.changes: list[tuple[Path, Path, dict]] = []
        "Path to the files to be changed and path to the original file and status"
        self.passed_away: set[Path] = set()
        "These paths were renamed etc."
        self.size_affected = 0
        "stats counter"
        self.affected_count = 0
        "stats counter"
        self.ignored_count = 0
        "Files skipped because previously renamed with deduplidog"
        self.having_multiple_candidates: dict[Path, list[Path]] = {}
        "What unsuccessful candidates did work files have?"
        self.bar: tqdm | None = None
        "Work files iterator"
        match self.tolerate_hour:
            case True:
                self.tolerate_hour = -1, 1
            case n if isinstance(n, int):
                self.tolerate_hour = -abs(n), abs(n)
            case _:
                raise AssertionError("Use whole hours only")
        self._files_cache = defaultdict(set)
        "Original files, grouped by stem"

        self._common_prefix_length = 0

        self.check()
        self.perform()

    def perform(self):

        # build file list of the originals
        if self.file_list:
            if not str(self.file_list[0]).startswith(str(self.original_dir)):
                print("Fail: We received cached file_list but it seems containing other directory than originals.")
                return
        else:
            self.file_list = Deduplidog.build_originals(self.original_dir, self.suffixes)
        print("Number of originals:", len(self.file_list))

        self._files_cache.clear()
        for p in self.file_list:
            p_case = Path(str(p).casefold()) if self.casefold else p
            self._files_cache[p_case.stem[:self.work_file_stem_shortened]].add(p)
        self._common_prefix_length = len(os.path.commonprefix([self.original_dir, self.work_dir])) \
            if self.shorter_log else 0

        # loop all files in the work dir and check them for duplicates amongst originals
        # try#
        # concurrent worker to rename files
        # we suppose this might be quicker than waiting the renaming IO action is done
        # BUT IT IS NOT AT ALL
        # self.queue = Queue()
        # worker = Thread(target=self._rename_worker, args=(self.queue,))
        # worker.start()

        try:
            self._loop_files()
        except:
            raise
        finally:
            if self.bar:
                print(f"Affected: {self.affected_count}/{self.bar.total- self.ignored_count}", end="")
                if self.ignored_count:
                    print(f" ({self.ignored_count} ignored)", end="")
                print("\nAffected size:", naturalsize(self.size_affected))
                print("Unsuccessful files having multiple candidates length:", len(self.having_multiple_candidates))
        #    self.queue.put(None)
        #    worker.join()
        #    print("Worker finished")

    # def _rename_worker(self, queue):
    #    while True:
    #        sleep(1)
    #        item = queue.get()
    #        if item is None:
    #            break
    #
    #        source_file, target_file = item
    #
    #        #affected_file.rename(affected_file.with_name("✓" + affected_file.name))
    #        source_file.rename(target_file)
    #        #print(f'>got {source_file} > {target_file}')
    #
    #    print('Renaming finished')

    def check(self):
        """ Checks setup and prints out the description. """
        if self.affect_only_if_smaller and not self.media_magic:
            raise AssertionError("The affect_only_if_smaller works only with media_magic")

        if self.media_magic:
            print("Only files with media suffixes are taken into consideration. Nor the size or date is compared.")
        else:
            print("Find files that has the same size " + ("(date is ignored)" if self.ignore_date else "and date") + ".")

        which = "either the file from the work dir or the original dir (whichever is bigger)" if self.treat_bigger_as_original else "duplicates"
        small = " (only if smaller than the pair file)" if self.affect_only_if_smaller else ""
        action = "will be" if self.execute else f"would be (if execute were True)"
        print(f"{which.capitalize()}{small} {action} ", end="")

        match self.rename, self.replace_with_original:
            case True, False:
                print("renamed (prefixed with ✓).")
            case False, True:
                print("replaced with the original.")
            case True, True:
                raise AssertionError("Choose either rename or replace_with_original")

        if self.set_both_to_older_date:
            print("Original file mtime date might be set backwards to the duplicate file.")

    def _loop_files(self):
        work_dir, skip = self.work_dir, self.skip
        work_files = [f for f in tqdm(Path(work_dir).rglob("*"), desc="Caching working files")]
        if skip:
            if isinstance(work_files, list):
                work_files = work_files[skip:]
            else:
                [next(work_files) for _ in range(skip)]
            print("Skipped", skip)
        self.bar = bar = tqdm(work_files)
        for work_file in bar:
            for attempt in range(5):
                try:
                    self._process_file(work_file, bar)
                except Image.DecompressionBombError as e:
                    print("Failing on exception", work_file, e)
                except Exception as e:
                    if self.fail_on_error:
                        raise
                    else:
                        sleep(1 * attempt)
                        print("Repeating on exception", work_file, e)
                        continue
                except KeyboardInterrupt:
                    print(f"Interrupted. You may proceed where you left with the skip={skip+bar.n} parameter.")
                    return bar
                break
        return bar

    def _process_file(self, work_file: Path, bar: tqdm):
        # work file name transformation
        name = str(work_file.name)
        if name.startswith("✓"):  # this file has been already processed
            self.ignored_count += 1
            return
        stem = str(work_file.stem)
        if self.space2char:
            stem = stem.replace(" ", self.space2char)
        if self.strip_end_counter:
            stem = self.ending_counter.sub("", stem)
        if self.strip_suffix:
            stem = re.sub(self.strip_suffix + "$", "", stem)
        if self.casefold:
            stem = stem.casefold()

        if work_file.is_symlink() or self.suffixes and work_file.suffix.lower() not in self.suffixes:
            return

        # print stats
        bar.set_postfix({"size": naturalsize(self.size_affected),
                         "affected": self.affected_count,
                         "file": str(work_file)[len(str(self.work_dir)):]
                         })

        # candidate = name matches
        _candidates_fact = (p for p in self._files_cache[stem] if
                            work_file != p
                            and p not in self.passed_away)

        if self.media_magic:
            # build a candidate list
            comparing_image = work_file.suffix.lower() in IMAGE_SUFFIXES
            candidates = [p for p in _candidates_fact if
                          # comparing images to images and videos to videos
                          p.suffix.lower() in (IMAGE_SUFFIXES if comparing_image else VIDEO_SUFFIXES)]

            # check candidates
            original = self._find_similar_media(work_file, comparing_image, candidates)
        else:
            # compare by date and size
            candidates = [p for p in _candidates_fact if p.suffix.casefold() == work_file.suffix.casefold()] \
                if self.casefold else [p for p in _candidates_fact if p.suffix == work_file.suffix]
            original = self._find_similar(work_file, candidates)

        # original of the work_file has been found
        # one of them might be treated as a duplicate and thus affected
        if original:
            self._affect(work_file, original)
        elif len(candidates) > 1:  # we did not find the object amongst multiple candidates
            self.having_multiple_candidates[work_file] = candidates
            logger.debug("Candidates %s %s", work_file, candidates)

    def _affect(self, work_file: Path, original: Path):
        # which file will be affected? The work file or the mistakenly original file?
        status = {work_file: [], original: []}
        affected_file, other_file = work_file, original
        warning = False
        if affected_file == other_file:
            logger.error("Error, the file is the same", affected_file)
            return
        if self.media_magic:  # why checking media_magic?
            # This is just a double check because if not media_magic,
            # the files must have the same size nevertheless.
            work_size, orig_size = work_file.stat().st_size, original.stat().st_size
            match self.treat_bigger_as_original, work_size > orig_size:
                case (True, True):
                    affected_file, other_file = original, work_file
                case (False, True):
                    status[work_file].append(f"SIZE WARNING {naturalsize(work_size-orig_size)}")
                    warning = True
            if self.affect_only_if_smaller and affected_file.stat().st_size >= other_file.stat().st_size:
                logger.debug("Skipping %s as it is smaller than %s", affected_file, other_file)  # TODO check
                return

        # execute changes or write a log
        self.size_affected += affected_file.stat().st_size
        self.affected_count += 1

        # setting date
        affected_date, other_date = affected_file.stat().st_mtime, other_file.stat().st_mtime
        match self.set_both_to_older_date, affected_date != other_date:
            case True, True:
                # dates are not the same and we want change them
                if other_date < affected_date:
                    self._change_file_date(affected_file, affected_date, other_date, status)
                elif other_date > affected_date:
                    self._change_file_date(other_file, other_date, affected_date, status)
            case False, True if (other_date > affected_date):
                # attention, we do not want to tamper dates however the file marked as duplicate has
                # lower timestamp (which might be genuine)
                status[other_file].append(f"DATE WARNING + {naturaldelta(other_date-affected_date)}")
                warning = True

        # renaming
        if self.rename:
            status_ = "renamable"
            if self.execute or self.bashify:
                # self.queue.put((affected_file, affected_file.with_name("✓" + affected_file.name)))
                target_path = affected_file.with_name("✓" + affected_file.name)
                if self.execute:
                    if target_path.exists():
                        err = f"Do not rename {affected_file} because {target_path} exists."
                        if self.fail_on_error:
                            raise FileExistsError(err)
                        else:
                            logger.warning(err)
                    else:
                        affected_file.rename(target_path)
                        status_ = "renaming"
                if self.bashify:
                    print(f"mv -n {_qp(affected_file)} {_qp(target_path)}")  # TODO check
                self.passed_away.add(affected_file)
            status[affected_file].append(status_)
        if self.replace_with_original:
            status_ = "replacable"
            if self.execute:
                status_ = "replacing"
                shutil.copy2(other_file, affected_file)
            if self.bashify:
                print(f"cp --preserve {_qp(other_file)} {_qp(affected_file)}")  # TODO check
            status[affected_file].append(status_)

        self.changes.append((work_file, original, status))
        suffix = " (affected):" if affected_file is original else ":"
        getattr(logger, "warning" if warning else "info")("Original%s %s %s",
                                                          suffix, self._path(original), " ".join(str(s) for s in status[original]))
        getattr(logger, "warning" if warning else "info")(
            "Work file: %s %s", self._path(work_file), " ".join(str(s) for s in status[work_file]))

    def _change_file_date(self, path, old_date, new_date, status):
        # Consider following usecase:
        # Duplicated file 1, date 14:06
        # Duplicated file 2, date 15:06
        # Original file,     date 18:00.
        # The status message will mistakingly tell that we change Original date to 14:06 (good), then to 15:06 (bad).
        # However, these are just the status messages. But as we resolve the dates at the launch time,
        # original date will end up as 14:06 because 15:06 will be later.
        status[path].extend(("redating" if self.execute else 'redatable',
                            datetime.fromtimestamp(old_date), "->", datetime.fromtimestamp(new_date)))
        if self.execute:
            os.utime(path, (new_date,)*2)  # change access time, modification time
        if self.bashify:
            print(f"touch -t {new_date} {_qp(path)}")  # TODO check

    def _path(self, path):
        """ Strips out common prefix that has originals with work_dir for display reasons.
            /media/user/disk1/Photos -> 1/Photos
            /media/user/disk2/Photos -> 2/Photos
        """
        return str(path)[self._common_prefix_length:]

    def _find_similar(self, work_file: Path, candidates: list[Path]):
        """ compare by date and size """
        for original in candidates:
            ost, wst = original.stat(), work_file.stat()
            if (self.ignore_date
                        or wst.st_mtime == ost.st_mtime
                        or self.tolerate_hour and self.tolerate_hour[0] <= (wst.st_mtime - ost.st_mtime)/3600 <= self.tolerate_hour[1]
                    ) and wst.st_size == ost.st_size:
                return original

    def _find_similar_media(self,  work_file: Path, comparing_image: bool, candidates: list[Path]):
        similar = False
        ref_time = False
        work_pil = None
        if self.debug:
            print("File", work_file, "\n", "Candidates", candidates)
        for original in candidates:
            if not original.exists():
                continue
            if comparing_image:  # comparing images
                if not ref_time:
                    ref_time = work_file.stat().st_mtime
                    work_pil = Image.open(work_file)
                similar = self.image_similar(original, work_file, work_pil, ref_time)
            else:  # comparing videos
                frame_delta = abs(get_frame_count(
                    work_file) - get_frame_count(original))
                similar = frame_delta <= self.accepted_frame_delta
                if not similar and self.debug:
                    print("Frame delta:", frame_delta, work_file, original)
            if similar:
                break
        return original if similar else False

    def image_similar(self, original: Path, work_file: Path, work_pil: Image, ref_time: float):
        """ Returns true if images are similar.
            When? If their image hash difference are relatively small.
            XIf original ref_time set
                ref_time: the file date of the investigated file f or its EXIF date
            has to be no more than an hour around.
        """
        try:
            similar = False
            original_pil = Image.open(original)

            # compare time
            if self.img_compare_date:
                try:
                    exif_times = {datetime.strptime(v, '%Y:%m:%d %H:%M:%S').timestamp() for k, v in original_pil._getexif().items() if
                                  k in ExifTags.TAGS and "DateTime" in ExifTags.TAGS[k]}
                except:
                    exif_times = tuple()
                file_time = original.stat().st_mtime
                similar = abs(ref_time - file_time) <= 3600 \
                    or any(abs(ref_time - t) <= 3600 for t in exif_times)
                # print("* čas",similar, original, ref_time, exif_times, file_time)

            if similar or not self.img_compare_date:
                hash0 = imagehash.average_hash(original_pil)
                hash1 = imagehash.average_hash(work_pil)
                # maximum bits that could be different between the hashes
                similar = abs(hash0 - hash1) <= self.accepted_img_hash_diff
                if not similar and self.debug:
                    print("Hash distance:", abs(hash0 - hash1))
            return similar
        except OSError as e:
            print(e, original, work_file)

    @staticmethod
    @cache
    def build_originals(original_dir: str | Path, suffixes: bool | tuple[str]):
        return [p for p in tqdm(Path(original_dir).rglob("*"), desc="Caching original files") if p.is_file() and not p.is_symlink() and (not suffixes or p.suffix.lower() in suffixes)]


def _qp(path: Path):
    """Quoted path. Output path to be used in bash. I wonder there is no system method which covers
    quotes in the path etc.
    """
    s = str(path)
    return f'"{s}"' if " " in s else s


def remove_prefix_in_workdir(work_dir: str):
    """ Removes the prefix ✓ recursively from all the files.
    The prefix might have been previously given by the deduplidog. """
    work_files = [f for f in tqdm(Path(work_dir).rglob("*"), desc="Caching working files") if f.is_file()]
    for p in work_files:
        p.rename(p.with_stem(p.stem.removeprefix("✓")))


def get_frame_count(filename):
    import cv2
    video = cv2.VideoCapture(str(filename))

    # duration = video.get(cv2.CAP_PROP_POS_MSEC)
    frame_count = video.get(cv2.CAP_PROP_FRAME_COUNT)

    return frame_count


def mark_symlink_by_target(suspicious_directory: str | Path, starting_path):
    """ If the file is a symlink, pointing to this path, rename it with an arrow

    :param suspicious_directory: Ex: /media/user/disk/Takeout/Photos/
    :param starting_path: Ex: /media/user/disk
    """
    for f in (x for x in Path(suspicious_directory).rglob("*") if x.is_symlink()):
        if str(f.resolve()).startswith(starting_path):
            print(f.rename(f.with_name("→" + f.name)))
            print(f)

# Opakovane vyhledavat, zde se soubory podobneho jmena naleza nekde v dane ceste.
# Zobrazit jako obrazky a nahledy videi vsechny takove soubory.


def _stub():
    while True:
        a = input()
        clear_output()
        cwd = "/media/user/disk1/Photos/"
        print("Searching", a, "in", cwd)
        files = find("-iname", f"*{a}*", _cwd=cwd)
        files = [Path(cwd, f.strip()) for f in files]
        print("Len", len(files))
        images(files)
        [print_video_thumbs(f) for f in files]


def _are_similar(original: Path, work_file: Path, accepted_img_hash_diff: int = 1):
    original_pil = Image.open(original)
    work_pil = Image.open(work_file)
    hash0 = imagehash.average_hash(original_pil)
    hash1 = imagehash.average_hash(work_pil)
    # maximum bits that could be different between the hashes
    return abs(hash0 - hash1) <= accepted_img_hash_diff


def are_contained(work_dir, original_dir, sec_range: int = 60):
    """ You got two dirs with files having different naming system (427.JPG vs DSC_1344)
        which you suspect to contain the same set. The same files in the dirs seem to have the same timestamp.
        The same timestamp means +/- sec_range (ex: 1 minute).
        Loop all files from work_dir and display corresponding files having the same timestamp.
        or warn that no original exists-
        """

    # build directory of originals
    global originals, found
    originals = defaultdict(set)  # [timestamp] = set(originals...)
    for of in Path(original_dir).rglob("*"):
        originals[of.stat().st_mtime].add(of)

    found = {}
    for wf in (bar := tqdm(list(Path(work_dir).rglob("*")))):
        bar.set_postfix({"file": str(wf.name), "found": len(found)})

        timestamp = wf.stat().st_mtime
        # 0, -1, 1, -2, 2 ... to find candidate earlier
        range_ = sorted(range(-sec_range, sec_range+1), key=lambda x: abs(x))
        corresponding = (originals.get(timestamp + i, set())
                         for i in range_)  # find all originals with similar timestamps
        # flatten the sets and unique them (but keep as list to preserve files with less timestamp difference first)
        corresponding = list(dict.fromkeys(chain.from_iterable(corresponding)))

        if corresponding:
            for candidate in (bar2 := tqdm(corresponding, leave=False, desc="Candidates")):
                bar2.set_postfix({"file": candidate.name})
                if _are_similar(candidate, wf):
                    found[wf] = candidate
                    # tqdm would not dissappear if not finished https://github.com/tqdm/tqdm/issues/1382
                    bar2.update(float("inf"))
                    bar2.close()
                    break
            else:
                print("No candidate for", wf.name, corresponding)
                images([wf] + list(corresponding))
        else:
            print("Missing originals for", wf.name)


# are_contained("/media/user/disk1/Photos/_tabor/2/", "/media/user/disk1/Photos/tabory/C 074 2016/")


def images(urls):
    """ Display a ribbon of images """
    images_ = []
    for url in tqdm(urls, leave=False):
        p = Path(url)
        if p.exists():
            images_.append(widgets.Image(width=150, value=p.read_bytes()))
        else:
            print("Fail", p)
    display(HBox(images_))


def print_video_thumbs(src):
    vidcap = cv2.VideoCapture(str(src))
    success, image = vidcap.read()
    count = 0
    images = []
    while success:
        success, image = vidcap.read()
        if count % 100 == 0:
            try:
                # images.append(Image(width=150, data=cv2.imencode('.jpg', image)[1]))
                images.append(widgets.Image(width=150, value=cv2.imencode('.jpg', image)[1]))
            except:
                break
            if count > 500:
                break
        count += 1
    print(src, get_frame_count(src))
    if images:
        display(HBox(images))


def get_video_thumbs(dir_):
    """ Abych rychle poznal, co v kterem videu je, vypsat delku a prvnich par screenu """
    for f in sorted(Path(dir_).rglob("*")):
        if f.suffix.lower() in (".mov", ".avi", ".mp4", ".vob"):
            print_video_thumbs(f)


get_video_thumbs("/media/user/disk1/Photos/dram/")


def mark_symlink_only_dirs(dir_):
    """ Pokud je adresar plny jen symlinku nebo prazdny, přijmenovat mu šipku """
    for d in (x for x in Path(dir_).rglob("*") if x.is_dir()):
        if all(x.is_symlink() for x in Path(d).glob("*")):
            print(d.rename(d.with_name("→" + d.name)))


# mark_symlink_only_dirs("/media/user/disk2/Takeoutuser/Google Photos/")


def mark_01_copies(suspicious_directory):
    for f in (x for x in Path(suspicious_directory).glob("*(1)*")):
        stem = f.stem.removesuffix("(1)")

        for x in (x for x in Path("/media/user/disk2/_duplikaty_smazat/").rglob("*") if x.stem.removeprefix("✓") == stem):
            print(f.rename(f.with_name("→" + f.name)))
# mark_01_copies("/media/user/disk2/Takeoutuser/YouTube and YouTube Music/videos/")


def mtime_files_in_dir_according_to_json(dir_, json_dir):
    """ google photos vrací json, kde je čas fotky
    Kromě JPG.
    """
    for photo in Path(dir_).rglob("*"):
        # if photo.suffix.lower() in (".jpg", ".jpeg"):
        #    continue
        # if "50607264_2240519186012556_9095104762705084416_o.jpg" not in photo.name:
        #    continue
        metadata = Path(json_dir).joinpath(photo.name[:46] + ".json")
        if metadata.exists():
            # if photo.stat().st_mtime < 1654812000:
            # zmenit jenom takove soubory, ktere uz nebyly zmeneny jinak,
            # coz poznam tak, ze jejich datum je 10.6.2022
            #    continue
            timestamp = json.loads(metadata.read_text())["photoTakenTime"]["timestamp"]
            os.utime(photo, (int(timestamp), int(timestamp)))
            print(photo)
            # break

# mtime_files_in_dir_according_to_json("/media/user/disk2/Takeoutuser/Google Photos/Photos from 2019/",
            # "/media/user/disk2/photos_json/")


# DISK1_PHOTOS = mdf.file_list
# NAHRAVKY_LIST = mdf.file_list
