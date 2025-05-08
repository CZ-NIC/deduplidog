import logging
import os
import re
import shutil
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache, partial
from pathlib import Path
from time import sleep
import traceback
from typing import Optional

from humanize import naturaldelta, naturalsize
from PIL import Image
from mininterface import Mininterface
from mininterface.facet import Image as FacetImage
from pillow_heif import register_heif_opener
from tqdm.autonotebook import tqdm
from tyro.conf import OmitArgPrefixes

from .helpers import FileMetadata, keydefaultdict
from .utils import _qp, crc, get_frame_count, open_log_file

VIDEO_SUFFIXES = ".mp4", ".mov", ".avi", ".vob", "ogv", "webm", ".mts", ".3gp", ".mpg", ".mpeg", ".wmv", ".hevc"
IMAGE_SUFFIXES = ".jpg", ".jpeg", ".jxl", ".png", ".gif", ".avif", ".webp", ".heic"
MEDIA_SUFFIXES = IMAGE_SUFFIXES + VIDEO_SUFFIXES

logger = logging.getLogger(__name__)
Change = dict[Path, list[str | datetime]]
"Lists changes performed/suggested to given path. First entry is the work file, the second is the original file."

register_heif_opener()


@dataclass
class Action:
    """ What is to be done with the duplicates. """

    execute: bool = False
    "If False, nothing happens, just a safe run is performed."

    inspect: bool = False
    """Print bash commands that correspond to the actions that would have been executed if execute were True.
     You can check and run them yourself."""

    rename: bool = False
    """If `execute=True`, prepend ✓ to the duplicated work file name (or possibly to the original file name if treat_bigger_as_original).
     Mutually exclusive with other execute action."""

    delete: bool = False
    """If `execute=True`, delete theduplicated work file name (or possibly to the original file name if treat_bigger_as_original).
     Mutually exclusive with other execute action."""

    replace_with_original: bool = False
    """If `execute=True`, replace duplicated work file with the original (or possibly vice versa if treat_bigger_as_original).
    Mutually exclusive with other execute action."""

    replace_with_symlink: bool = False
    """If `execute=True`, replace duplicated work file with the relative symlink to the original (or possibly vice versa if treat_bigger_as_original). Its modification time is kept.
    Mutually exclusive with other execute action."""


@dataclass
class Execution:
    """ Parameters affecting the way the execution runs. """

    set_both_to_older_date: bool = False
    "If `execute=True`, `media_magic=True` or (media_magic=False and `ignore_date=True`), both files are set to the older date. Ex: work file get's the original file's date or vice versa."

    treat_bigger_as_original: bool = False
    "If `execute=True` and `rename=True` and `media_magic=True`, the original file might be affected (by renaming) if smaller than the work file."

    skip_bigger: bool = False
    """If `media_magic=True`, all writing actions, such as `rename`, `replace_with_original`, `set_both_to_older_date` and `treat_bigger_as_original`
     are executed only if the affectable file is smaller (or the same size) than the other."""

    skip_empty: bool = False
    "Skip files with zero size."

    neglect_warning: bool = False
    "By default, when a file with bigger size or older date should be affected, just warning is generated. Turn this to suppress it."

    confirm_one_by_one: bool = True
    """ Instead of executing changes all at once, confirm one by one.
        So that you may decide whether the media similarity detection works.
        If a warning occurs, the default is 'no' to perform the action. """


@dataclass
class Match:
    """ The way the files are compared. """

    casefold: bool = False
    "Case insensitive file name comparing."
    checksum: bool = False
    """If `media_magic=False` and `ignore_size=False`, files will be compared by CRC32 checksum.
    (This mode is considerably slower.)"""
    tolerate_hour: int | tuple[int, int] | bool = False  # TODO False will make mininterface produce a checkbox
    """When comparing files in work_dir and `media_magic=False`, tolerate hour difference.
        Sometimes when dealing with FS changes, files might got shifted few hours.
        * bool → -1 .. +1
        * int → -int .. +int
        * tuple → int1 .. int2
        Ex: tolerate_hour=2 → work_file.st_mtime -7200 ... + 7200 is compared to the original_file.st_mtime """

    ignore_name: bool = False
    "Files will not be compared by stem nor suffix."

    ignore_date: bool = False
    "If `media_magic=False`, files will not be compared by date."

    ignore_size: bool = False
    "If `media_magic=False`, files will not be compared by size."

    space2char: bool = False
    """When comparing files in work_dir, consider space as another char. Ex: "file 012.jpg" is compared as "file_012.jpg" """
    strip_end_counter: bool = False
    """When comparing files in work_dir, strip the counter. Ex: "00034(3).MTS" is compared as "00034.MTS" """
    strip_suffix: str = ""
    """When comparing files in work_dir, strip the file name end matched by a regular. Ex: "001-edited.jpg" is compared as "001.jpg" """

    work_file_stem_shortened: int | None = None
    "Photos downloaded from Google have its stem shortened to 47 chars. For the comparing purpose, treat original folder file names shortened."

    invert_selection: bool = False
    "Match only those files from work_dir that does not match the criterions."


@dataclass
class Media:
    """ Media files similarity detection. """

    media_magic: bool = False
    """ Media files similarity detection.
    Neither the size, date nor suffix is compared for files with media suffixes.
    A video is considered a duplicate if it has the same name and a similar number of frames, even if it has a different extension.
    An image is considered a duplicate if it has the same name and a similar image hash, even if the files are of different sizes.
    (This mode is considerably slower.)
    """

    accepted_frame_delta: int = 1
    "Number of frames for which two videos are considered equal."

    accepted_img_hash_diff: int = 1
    "Hash difference between images so that they are considered equal, see https://github.com/JohannesBuchner/imagehash"

    img_compare_date: bool = False
    "If True and `media_magic=True`, the work file date or the work file EXIF date must match the original file date (has to be no more than an hour around)."
    img_max_size: int = 0
    "Used only when media_magic is True. In the beginning, we preload the image hash of all the img in the original folder. This makes the hash calculation preload to skip if the file is bigger than this bytes. If you are searching for a relatively small image duplicates, you boost the original image hash caching speed by skipping the large ones."


@dataclass
class Helper:
    """ Helper settings. """

    log_level: int = logging.WARNING
    "10 debug .. 50 critical"  # NOTE check

    output: bool = False
    "Stores the output log to a file in the current working directory. (Never overwrites an older file.)"

    # NOTE bashize should be outputtable through output


@dataclass
class Deduplidog:
    """
    Find the duplicates.

    Normally, the file must have the same size, date and name. (Name might be just similar if parameters like strip_end_counter are set.)

    If `media_magic=True`, media files receive different rules: Neither the size nor the date are compared. See its help.
    """

    action: OmitArgPrefixes[Action]
    execution: OmitArgPrefixes[Execution]
    match: OmitArgPrefixes[Match]
    media: OmitArgPrefixes[Media]
    helper: OmitArgPrefixes[Helper]

    work_dir: Path = Path.cwd()
    """Folder of the files suspectible to be duplicates."""

    original_dir: Path | None = None
    """Folder of the original files. Normally, these files will not be affected.
        (However, they might get affected by `treat_bigger_as_original` or `set_both_to_older_date`)."""

    # Following parameters are undocumented:
    suffixes: Optional[list[str]] = None
    "If set, only files with such suffixes are compared. Ex: `suffixes = MEDIA_SUFFIXES`"

    skip: int = 0
    "Skip first n files in work_dir. Useful when a big task is interrupted and we want to continue without checking again the first part that has already been processed."

    debug: bool | None = None
    fail_on_error: bool = False
    shorter_log: bool = True
    "NOTE deprecated If True, common prefix of the file names are not output to the log to save space."

    ending_counter = re.compile(r"\(\d+\)$")

    def __repr__(self):
        text = ', '.join(f'{attr}={len(v) if isinstance(v, (set, list, dict)) else v}' for attr,
                         v in vars(self).items())
        return f'Deduplidog({text})'

    def __post_init__(self):
        logging.basicConfig(level=self.helper.log_level, format="%(message)s", force=True)
        logger.setLevel(self.helper.log_level)
        [handler.setLevel(self.helper.log_level) for handler in logger.handlers]

        self.file_list: Optional[list[Path]] = None
        "Use original file list. If none, a new is generated or a cached version is used."

        self.work_files: list[Path] = field(default_factory=list)
        "All files in work dir."

        self.changes: list[Change] = []
        "Path to the files to be changed and path to the original file and status"
        self.passed_away: set[Path] = set()
        "These paths were renamed etc."

        self.bar: tqdm | None = None
        "Work files iterator"
        self._files_cache: dict[str, set[Path]] = defaultdict(set)
        "Original files, grouped by stem"
        self.metadata: dict[Path, FileMetadata] = keydefaultdict(FileMetadata)
        "File metadata like stat() (which is not cached by default)"
        self._common_prefix_length = 0
        " NOTE deprecated"
        self.original_dir_name = self.work_dir_name = None
        "Shortened name, human readable"
        self.same_superdir = False
        """ Work_dir and original dir is the same """
        self._output = None
        " Log buffer "

        # Resetable part
        self.size_affected = 0
        "stats counter"
        self.affected_count = 0
        "stats counter"
        self.warning_count = 0
        "stats counter"
        self.ignored_count = 0
        "Files skipped because previously renamed with deduplidog"
        self.having_multiple_candidates: dict[Path, list[Path]] = {}
        "What unsuccessful candidates did work files have?"

        self.m = Mininterface()

    def reset(self):
        self.size_affected = 0
        self.affected_count = 0
        self.warning_count = 0
        self.ignored_count = 0
        self.having_multiple_candidates.clear()

    def start(self, interface=None):
        if interface:
            self.m = interface
        self.reset()
        self.check()
        self.perform()
        return self

    def perform(self):
        # build file list of the originals
        if self.file_list:
            if not str(self.file_list[0]).startswith(str(self.original_dir)):
                print("Fail: We received cached file_list but it seems containing other directory than originals.")
                return
        else:
            self.file_list = Deduplidog.build_originals(self.original_dir, tuple(self.suffixes or ()))

        self._files_cache.clear()
        not_computed = 0
        self.work_files = [f for f in tqdm(
            (p for p in Path(self.work_dir).rglob("*") if not p.is_dir()), desc="Caching working files")]

        if not self.match.ignore_name:
            for p in self.file_list:
                p_case = Path(str(p).match.casefold()) if self.match.casefold else p
                self._files_cache[p_case.stem[:self.match.work_file_stem_shortened]].add(p)
        elif self.media.media_magic:
            # We preload the metadata cache, since we think there will be a lot of candidates.
            # This is because media_magic does not use date nor size file filtering so evaluating the first work_file might
            # take ages. Here, we put a nice progress bar.
            not_computed = self.preload_metadata(self.file_list, self.work_files)

        orig_count = len(self.file_list) - not_computed
        if not orig_count:
            print("No originals to be compared.")
            return
        print("Number of originals:", orig_count)

        self._common_prefix_length = len(os.path.commonprefix([self.original_dir, self.work_dir])) \
            if self.shorter_log else 0

        if self.helper.output:
            name = ",".join([self.original_dir_name, self.work_dir_name] +
                            [p for p, v in vars(self).items() if v is True])[:150]
            self._output = open_log_file(name)
        try:
            self._loop_files()
        except:
            raise
        finally:
            if self._output:
                self._output.close()
            if self.bar:
                print()
                print(f"{'Affected' if self.action.execute else 'Affectable'}:"
                      f" {self.affected_count}/{len(self.work_files) - self.ignored_count}", end="")
                if self.ignored_count:
                    print(f" ({self.ignored_count} ignored)", end="")
                print(f"\n{'Affected' if self.action.execute else 'Affectable'} size:", naturalsize(self.size_affected))
                if self.warning_count:
                    print(f"Warnings: {self.warning_count}")
                if self.having_multiple_candidates:
                    print("Unsuccessful files having multiple candidates length:", len(self.having_multiple_candidates))

    def preload_metadata(self, files: list[Path], work_files: list[Path]) -> int:
        """ Populate self.metadata with performance-intensive file information.

        We return the number of images whose hash was not computed.
        """
        if not any(x for x in work_files if x.suffix.lower() in IMAGE_SUFFIXES):
            logger.info("Do not preload metadata as there is no image among work files to be compared to.")
            return 0

        # Strangely, when I removed cached_properties from FileMetadata in order to be serializable for multiprocesing,
        # using ThreadPoolExecutor is just as quick as ProcessPoolExecutor
        # as it spans the threads over multiple cores too.
        # I thought ThreadPoolExecutor spans just on a single core.
        images = [x for x in files if x.suffix.lower() in IMAGE_SUFFIXES]
        with ProcessPoolExecutor(max_workers=4) as executor:
            for fm in tqdm(executor.map(partial(FileMetadata.preload, max_size=self.media.img_max_size), images),
                           total=len(images),
                           desc="Caching image hashes"):
                self.metadata[fm.file] = fm
        return sum(1 for fm in self.metadata.values() if not fm.average_hash)

    def check(self):
        """ Checks setup and prints out the description. """

        # Distinguish paths
        if not self.original_dir:
            self.original_dir = self.work_dir
        if not self.work_dir:
            raise AssertionError("Missing work_dir")
        else:
            self.same_superdir = False
            for a, b in zip(Path(self.work_dir).parts, Path(self.original_dir).parts):
                if a != b:
                    self.work_dir_name = a
                    self.original_dir_name = b
                    break
            else:
                self.same_superdir = True
                self.original_dir_name = self.work_dir_name = a

        if self.execution.skip_bigger and not self.media.media_magic:
            raise AssertionError("The skip_bigger works only with media_magic")

        if self.match.invert_selection and any((self.action.replace_with_original, self.execution.treat_bigger_as_original, self.execution.set_both_to_older_date)):
            raise AssertionError(
                "It does not make sense using invert_selection with this command. The work file has no file to compare to.")

        match self.match.tolerate_hour:
            case True:
                self.match.tolerate_hour = -1, 1
            case False:  # since bool is instance of int, we have to check this too
                pass
            case n if isinstance(n, int):
                self.match.tolerate_hour = -abs(n), abs(n)
            case n if isinstance(n, tuple) and all(isinstance(x, int) for x in n):
                pass
            case _:
                raise AssertionError("Use whole hours only")

        if self.match.ignore_name and self.match.ignore_date and self.match.ignore_size:
            raise AssertionError("You cannot ignore everything.")

        if self.media.media_magic:
            print("Only files with media suffixes are taken into consideration."
                  f" Nor the size nor the date is compared.{' Nor the name!' if self.match.ignore_name else ''}")
        else:
            if self.match.ignore_size and self.match.checksum:
                raise AssertionError("Checksum cannot be counted when ignore_size.")
            used, ignored = (", ".join(filter(None, x)) for x in zip(
                self.match.ignore_name and ("", "name") or ("name", ""),
                self.match.ignore_size and ("", "size") or ("size", ""),
                self.match.ignore_date and ("", "date") or ("date", ""),
                self.match.checksum and ("crc32", "") or ("", "crc32")))
            print(f"Find files by {used}{f', ignoring: {ignored}' if ignored else ''}")

        dirs_ = "" if self.same_superdir else \
            f" at '{self.work_dir_name}' or the original dir at '{self.original_dir_name}'"
        which = f"either the file from the work dir{dirs_} (whichever is bigger)" \
            if self.execution.treat_bigger_as_original \
            else f"duplicates from the work dir at '{self.work_dir_name}'"
        small = " (only if smaller than the pair file)" if self.execution.skip_bigger else ""
        nonzero = " with non-zero size" if self.execution.skip_empty else ""
        action = "will be" if self.action.execute else f"would be (if execute were True)"
        print(f"{which.capitalize()}{small}{nonzero} {action} ", end="")

        print(self._get_action(passive=True) + ".")

        if self.execution.set_both_to_older_date:
            print("Original file mtime date might be set backwards to the duplicate file.")
        print("")  # sometimes, this line is consumed

    def _get_action(self, passive=False):
        action = self.action.rename, self.action.replace_with_original, self.action.delete, self.action.replace_with_symlink
        if not sum(action):
            return f"{'left' if passive else 'leave'} intact (because no action is selected, nothing will happen)"
        elif sum(action) > 1:
            raise AssertionError("Choose only one execute action (like only rename).")
        elif self.action.rename:
            return f"rename{'d' * passive} (prefixed with ✓)"
        elif self.action.replace_with_original:
            return f"replace{'d' * passive} with the original"
        elif self.action.delete:
            return f"delete{'d' * passive}"
        elif self.action.replace_with_symlink:
            return f"replace{'d' * passive} with the symlink"

    def _loop_files(self):
        skip = self.skip
        work_files = self.work_files
        if skip:
            if isinstance(work_files, list):
                work_files = work_files[skip:]
            else:
                [next(work_files) for _ in range(skip)]
            print("Skipped", skip)
        self.bar = bar = tqdm(work_files, leave=False)
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
                        tb = traceback.format_tb(e.__traceback__)
                        print("Repeating on exception", work_file, e, tb[-1])
                        continue
                except KeyboardInterrupt:
                    print(f"Interrupted. You may proceed where you left with the skip={skip+bar.n} parameter.")
                    return
                break

    def _process_file(self, work_file: Path, bar: tqdm):
        # work file name transformation
        name = str(work_file.name)
        if name.startswith("✓"):  # this file has been already processed
            self.ignored_count += 1
            return
        stem = str(work_file.stem)
        if self.match.space2char:
            stem = stem.replace(" ", self.match.space2char)
        if self.match.strip_end_counter:
            stem = self.ending_counter.sub("", stem)
        if self.match.strip_suffix:
            stem = re.sub(self.match.strip_suffix + "$", "", stem)
        if self.match.casefold:
            stem = stem.match.casefold()

        if work_file.is_symlink() or self.suffixes and work_file.suffix.lower() not in self.suffixes:
            logger.debug("Skipping symlink or a non-wanted suffix: %s", work_file)
            return
        if self.execution.skip_empty and not work_file.stat().st_size:
            logger.debug("Skipping zero size: %s", work_file)
            return

        # print stats
        bar.set_postfix({"size": naturalsize(self.size_affected),
                         "affected": self.affected_count,
                         "file": str(work_file)[len(str(self.work_dir)):]
                         })

        # candidate = name matches
        _candidates_fact = (p for p in (self.file_list if self.match.ignore_name else self._files_cache[stem]) if
                            work_file != p
                            and p not in self.passed_away)

        if self.media.media_magic:
            # build a candidate list
            comparing_image = work_file.suffix.lower() in IMAGE_SUFFIXES
            candidates = [p for p in _candidates_fact if
                          # comparing images to images and videos to videos
                          p.suffix.lower() in (IMAGE_SUFFIXES if comparing_image else VIDEO_SUFFIXES)]

            # check candidates
            original = self._find_similar_media(work_file, comparing_image, candidates)
        else:
            # compare by date and size
            candidates = [p for p in _candidates_fact if p.suffix.match.casefold() == work_file.suffix.match.casefold()] \
                if self.match.casefold else [p for p in _candidates_fact if p.suffix == work_file.suffix]
            original = self._find_similar(work_file, candidates)

        # original of the work_file has been found
        # one of them might be treated as a duplicate and thus affected
        if original and not self.match.invert_selection:
            self._affect(work_file, original)
        elif not original and self.match.invert_selection:
            self._affect(work_file, Path("/dev/null"))
        elif len(candidates) > 1:  # we did not find the object amongst multiple candidates
            self.having_multiple_candidates[work_file] = candidates
            logger.debug("Candidates %s %s", work_file, candidates)

    def _affect(self, work_file: Path, original: Path):
        # which file will be affected? The work file or the mistakenly original file?
        change = {work_file: [], original: []}
        affected_file, other_file = work_file, original
        warning: Path | bool = False
        if affected_file == other_file:
            logger.error("Error, the file is the same", affected_file)
            return
        if self.media.media_magic:  # why checking media_magic?
            # This is just a double check because if not media_magic,
            # the files must have the same size nevertheless.)
            work_size, orig_size = work_file.stat().st_size, original.stat().st_size
            match self.execution.treat_bigger_as_original, work_size > orig_size:
                case True, True:
                    affected_file, other_file = original, work_file
                case False, True:
                    change[work_file].append(f"SIZE WARNING {naturalsize(work_size-orig_size)}")
                    warning = work_file
            if self.execution.skip_bigger and affected_file.stat().st_size > other_file.stat().st_size:
                logger.debug("Skipping %s as it is not smaller than %s", affected_file, other_file)
                return

        # execute changes or write a log

        # setting date
        affected_date, other_date = affected_file.stat().st_mtime, other_file.stat().st_mtime
        match self.execution.set_both_to_older_date, affected_date != other_date:
            case True, True:
                # dates are not the same and we want change them
                if other_date < affected_date:
                    self._change_file_date(affected_file, affected_date, other_date, change)
                elif other_date > affected_date:
                    self._change_file_date(other_file, other_date, affected_date, change)
            case False, True if other_date > affected_date and other_date-affected_date >= 1:
                # Attention, we do not want to tamper dates however the file marked as duplicate has
                # lower timestamp (which might be hint it is the genuine one).
                # However, too often I came into the cases when the difference was lower than a second.
                # So we neglect a lower-than-a-second difference.
                change[other_file].append(f"DATE WARNING + {naturaldelta(other_date-affected_date)}")
                warning = other_file

        if self.execution.confirm_one_by_one and not self._confirm(affected_file, other_file, change):
            # NOTE we can resolve the warning in the dialog too
            return
        if warning and not self.execution.neglect_warning:
            change[warning].append("🛟skipped on warning")
        else:
            self.size_affected += affected_file.stat().st_size
            self.affected_count += 1

            # other actions
            if self.action.rename:
                self._rename(change, affected_file)

            if self.action.delete:
                self._delete(change, affected_file)

            if self.action.replace_with_original:
                self._replace_with_original(change, affected_file, other_file)

            if self.action.replace_with_symlink:
                self._replace_with_symlink(change, affected_file, other_file)

        self.changes.append(change)
        if warning:
            self.warning_count += 1
        if (warning and self.helper.log_level <= logging.WARNING) or (self.helper.log_level <= logging.INFO):
            self.bar.clear()  # this looks the same from jupyter and much better from terminal (does not leave a trace of abandoned bars)
            self._print_change(change)
        if self._output:
            with redirect_stdout(self._output):
                self._print_change(change)

    def _confirm(self, affected_file, other_file, change: Change):
        els = []
        is_yes = True

        def add_file_elements(title, file):
            nonlocal is_yes
            els.extend([title, file])
            if file.suffix.lower() in IMAGE_SUFFIXES:
                els.append(FacetImage(file))
            if t := change[file]:
                els.extend(t)
                is_yes = False

        add_file_elements("Going to affect", affected_file)
        add_file_elements("Original", other_file)

        self.m.facet._layout(els)
        return self.m.confirm(self._get_action().capitalize(), is_yes)

    def _rename(self, change: Change, affected_file: Path):
        msg = "renamable"
        if self.action.execute or self.action.inspect:
            # self.queue.put((affected_file, affected_file.with_name("✓" + affected_file.name)))
            target_path = affected_file.with_name("✓" + affected_file.name)
            if self.action.execute:
                if target_path.exists():
                    err = f"Do not rename {affected_file} because {target_path} exists."
                    if self.fail_on_error:
                        raise FileExistsError(err)
                    else:
                        logger.warning(err)
                else:
                    affected_file.rename(target_path)
                    msg = "renaming"
            if self.action.inspect:
                self._inspect_print(f"mv -n {_qp(affected_file)} {_qp(target_path)}")
            self.passed_away.add(affected_file)
            self.metadata.pop(affected_file, None)
        change[affected_file].append(msg)

    def _delete(self, change: Change, affected_file: Path):
        msg = "deletable"
        if self.action.execute or self.action.inspect:
            if self.action.execute:
                affected_file.unlink()
                msg = "deleting"
            if self.action.inspect:
                self._inspect_print(f"rm {_qp(affected_file)}")
            self.passed_away.add(affected_file)
            self.metadata.pop(affected_file, None)
        change[affected_file].append(msg)

    def _replace_with_original(self, change: Change, affected_file: Path, other_file: Path):
        msg = "replacable"
        if other_file.name == affected_file.name:
            if self.action.execute:
                msg = "replacing"
                shutil.copy2(other_file, affected_file)
            if self.action.inspect:
                self._inspect_print(f"cp --preserve {_qp(other_file)} {_qp(affected_file)}")  # NOTE check
        else:
            if self.action.execute:
                msg = "replacing"
                shutil.copy2(other_file, affected_file.parent)
                affected_file.unlink()
            if self.action.inspect:
                # NOTE check
                self._inspect_print(f"cp --preserve {_qp(other_file)} {_qp(affected_file.parent)}"
                                    f" && rm {_qp(affected_file)}")
        change[affected_file].append(msg)
        self.metadata.pop(affected_file, None)

    def _replace_with_symlink(self, change: Change, affected_file: Path, other_file: Path):
        msg = "symlinkable"
        old_time = self.metadata[affected_file].stat.st_mtime
        if self.action.execute:
            msg = "symlinking"
            affected_file.unlink()
            affected_file.symlink_to(os.path.relpath(other_file, os.path.dirname(affected_file)))
            os.utime(affected_file, (old_time,)*2, follow_symlinks=False)
        if self.action.inspect:
            self._inspect_print(f"ln -sfr {_qp(other_file)} {_qp(affected_file)}"
                                f" && touch -h -t {datetime.fromtimestamp(old_time).strftime('%Y%m%d%H%M.%S')} {_qp(affected_file)}")
        change[affected_file].append(msg)
        self.passed_away.add(affected_file)
        self.metadata.pop(affected_file, None)

    def _change_file_date(self, path, old_date: float, new_date: float, change: Change):
        # Consider following usecase:
        # Duplicated file 1, date 14:06
        # Duplicated file 2, date 15:06
        # Original file,     date 18:00.
        # The status message will mistakingly tell that we change Original date to 14:06 (good), then to 15:06 (bad).
        # However, these are just the status messages. But as we resolve the dates at the launch time,
        # original date will end up as 14:06 because 15:06 will be later.
        change[path].extend(("redating" if self.action.execute else 'redatable',
                            datetime.fromtimestamp(old_date), "->", datetime.fromtimestamp(new_date)))
        if self.action.execute:
            os.utime(path, (new_date,)*2)  # change access time, modification time
            self.metadata.pop(path, None)
        if self.action.inspect:
            self._inspect_print(
                f"touch -t {datetime.fromtimestamp(new_date).strftime('%Y%m%d%H%M.%S')} {_qp(path)}")  # NOTE check

    def _path(self, path):
        """ Strips out common prefix that has originals with work_dir for display reasons.
            /media/user/disk1/Photos -> 1/Photos
            /media/user/disk2/Photos -> 2/Photos

            TODO May use self.work_file_name
        """
        return str(path)[self._common_prefix_length:]

    def _find_similar(self, work_file: Path, candidates: list[Path]):
        """ compare by date and size """
        for original in candidates:
            ost, wst = original.stat(), work_file.stat()
            if (self.match.ignore_date
                        or wst.st_mtime == ost.st_mtime
                        or self.match.tolerate_hour and self.match.tolerate_hour[0] <= (wst.st_mtime - ost.st_mtime)/3600 <= self.match.tolerate_hour[1]
                    ) and (self.match.ignore_size or wst.st_size == ost.st_size and (not self.match.checksum or crc(original) == crc(work_file))):
                return original

    def _find_similar_media(self,  work_file: Path, comparing_image: bool, candidates: list[Path]):
        similar = False
        work_cache = self.metadata[work_file]
        if self.debug:
            print("File", work_file, "\n", "Candidates", candidates)
        for orig_file in candidates:
            if not orig_file.exists():
                continue
            if comparing_image:  # comparing images
                similar = self.image_similar(self.metadata[orig_file], work_cache)
            else:  # comparing videos
                frame_delta = abs(get_frame_count(work_file) - get_frame_count(orig_file))
                similar = frame_delta <= self.media.accepted_frame_delta
                if not similar and self.debug:
                    print("Frame delta:", frame_delta, work_file, orig_file)
            if similar:
                break
        work_cache.clean()
        return orig_file if similar else False

    def image_similar(self, orig_cache: FileMetadata, work_cache: FileMetadata):
        """ Returns true if images are similar.
            When? If their image hash difference are relatively small.
        """
        try:
            similar = False
            # compare time
            if self.media.img_compare_date:
                exif_times = orig_cache.exif_times
                file_time = orig_cache.stat.st_mtime
                ref_time = work_cache.stat.st_mtime
                similar = abs(ref_time - file_time) <= 3600 \
                    or any(abs(ref_time - t) <= 3600 for t in exif_times)

            if similar or not self.media.img_compare_date:
                hash0 = orig_cache.average_hash
                hash1 = work_cache.average_hash
                if not hash0 or not hash1:
                    similar = False
                    hash_dist = "failed"
                else:
                    # maximum bits that could be different between the hashes
                    hash_dist = abs(hash0 - hash1)
                    similar = hash_dist <= self.media.accepted_img_hash_diff
                if not similar and self.debug:
                    print("Hash distance:", hash_dist)
            return similar
        except OSError as e:
            logger.error("OSError %s %s %s", e, orig_cache.file, work_cache.file)
        finally:
            orig_cache.clean()

    @staticmethod
    @cache
    def build_originals(original_dir: str | Path, suffixes: Optional[tuple[str]]):
        return [p for p in tqdm(Path(original_dir).rglob("*"), desc="Caching original files", leave=False)
                if p.is_file()
                and not p.is_symlink()
                and (not suffixes or p.suffix.lower() in suffixes)]

    def print_changes(self):
        "Prints performed/suggested changes to be inspected in a human readable form."
        [self._print_change(change) for change in self.changes]

    def _print_change(self, change: Change):
        """ We aim for the clearest representation to help the user orientate at a glance.
        Because file paths can be long, we'll display them as succinctly as possible.
        Sometimes we'll use, for example, the disk name, other times we'll use file names,
        or the first or last differing part of the path. """
        wicon, oicon = "🔨", "📄"
        wf, of = change

        # Nice paths
        wn, on = self.work_dir_name, self.original_dir_name  # meaningful dir representation
        if self.same_superdir:
            if wf.name == of.name:  # full path that makes the difference
                len_ = len(os.path.commonprefix((wf, of)))
                wn, on = str(wf.parent)[len_:] or "(basedir)", str(of.parent)[len_:] or "(basedir)"
            else:  # the file name will make the meaningful difference
                wn, on = wf.name, of.name
        print()
        print("*", wf)
        print(" ", of)
        [print(text, *(str(s) for s in changes))
            for text, changes in zip((f"  {wicon}{wn}:",
                                      f"  {oicon}{on}:"), change.values()) if len(changes)]

    def _inspect_print(self, text):
        if self._output:
            self._output.write(text + "\n")
        else:
            print(text)
