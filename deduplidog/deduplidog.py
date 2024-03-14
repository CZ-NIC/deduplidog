from contextlib import redirect_stdout
import logging
import os
import re
import shutil
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from time import sleep
from typing import Annotated, get_args, get_type_hints

import click
from dataclass_click import option
from humanize import naturaldelta, naturalsize
from PIL import Image
from pillow_heif import register_heif_opener
from tqdm.autonotebook import tqdm

from .helpers import Field, FileMetadata, keydefaultdict
from .utils import _qp, crc, get_frame_count, open_log_file

VIDEO_SUFFIXES = ".mp4", ".mov", ".avi", ".vob", ".mts", ".3gp", ".mpg", ".mpeg", ".wmv", ".hevc"
IMAGE_SUFFIXES = ".jpg", ".jpeg", ".png", ".gif", ".avif", ".webp", ".heic", ".avif"
MEDIA_SUFFIXES = IMAGE_SUFFIXES + VIDEO_SUFFIXES

logger = logging.getLogger(__name__)
Change = dict[Path, list[str | datetime]]
"Lists changes performed/suggested to given path. First entry is the work file, the second is the original file."

register_heif_opener()

# Unfortunately, instead of writing brief docstrings, Python has no regular way to annotate dataclass attributes.
# As mere strings are not kept in the runtime, we have to use cubersome Annotated syntax.
# Pros: We do not have to duplicate the copy the text while using TUI and CLI.
# Cons:
#   Help text is not displayed during static analysis (as an IDE hint).
#   We have to write the default value twice. (For the CLI and for the direct import to i.e. a jupyter notebook.)


def flag(help):
    "CLI support"
    return option(help=help, is_flag=True, default=False)


def conversion(_ctx, option, value):
    return Field(option.name,
                 value,
                 get_args(get_type_hints(Deduplidog, include_extras=True)[option.name])[0]) \
        .convert()


def opt(help, default, process_by_click=True):
    "CLI support"
    return option(help=help, default=default, type=None if process_by_click else click.UNPROCESSED, callback=conversion)


@dataclass
class Deduplidog:
    """
    Find the duplicates.

    Normally, the file must have the same size, date and name. (Name might be just similar if parameters like strip_end_counter are set.)

    If `media_magic=True`, media files receive different rules: Neither the size nor the date are compared. See its help.
    """

    work_dir: Annotated[str | Path, option(
        help="""Folder of the files suspectible to be duplicates.""", required=True, type=click.UNPROCESSED)]
    original_dir: Annotated[str | Path, option(
        help="""Folder of the original files. Normally, these files will not be affected.
        (However, they might get affected by `treat_bigger_as_original` or `set_both_to_older_date`).""", default="", type=click.UNPROCESSED)] = ""

    # Action section
    execute: Annotated[bool, flag(
        "If False, nothing happens, just a safe run is performed.")] = False
    bashify: Annotated[bool, flag(
        """Print bash commands that correspond to the actions that would have been executed if execute were True.
     You can check and run them yourself.""")] = False
    rename: Annotated[bool, flag(
        """If `execute=True`, prepend ✓ to the duplicated work file name (or possibly to the original file name if treat_bigger_as_original).
     Mutually exclusive with `replace_with_original` and `delete`.""")] = False
    delete: Annotated[bool, flag(
        """If `execute=True`, delete theduplicated work file name (or possibly to the original file name if treat_bigger_as_original).
     Mutually exclusive with replace_with_original and rename.""")] = False
    replace_with_original: Annotated[bool, flag(
        """If `execute=True`, replace duplicated work file with the original (or possibly vice versa if treat_bigger_as_original).
    Mutually exclusive with rename and delete.""")] = False
    set_both_to_older_date: Annotated[bool, flag(
        "If `execute=True`, `media_magic=True` or (media_magic=False and `ignore_date=True`), both files are set to the older date. Ex: work file get's the original file's date or vice versa.")] = False
    treat_bigger_as_original: Annotated[bool, flag(
        "If `execute=True` and `rename=True` and `media_magic=True`, the original file might be affected (by renaming) if smaller than the work file.")] = False
    skip_bigger: Annotated[bool, flag(
        """If `media_magic=True`, all writing actions, such as `rename`, `replace_with_original`, `set_both_to_older_date` and `treat_bigger_as_original`
     are executed only if the affectable file is smaller (or the same size) than the other.""")] = False
    skip_empty: Annotated[bool, flag("Skip files with zero size.")] = False
    neglect_warning: Annotated[bool, flag(
        "By default, when a file with bigger size or older date should be affected, just warning is generated. Turn this to suppress it.")] = False

    # Match section
    casefold: Annotated[bool, flag(
        "Case insensitive file name comparing.")] = False
    checksum: Annotated[bool, flag(
        """If `media_magic=False` and `ignore_size=False`, files will be compared by CRC32 checksum.
    (This mode is considerably slower.)""")] = False
    tolerate_hour: Annotated[int | tuple[int, int] | bool, opt(
        """When comparing files in work_dir and `media_magic=False`, tolerate hour difference.
        Sometimes when dealing with FS changes, files might got shifted few hours.
        * bool → -1 .. +1
        * int → -int .. +int
        * tuple → int1 .. int2
        Ex: tolerate_hour=2 → work_file.st_mtime -7200 ... + 7200 is compared to the original_file.st_mtime """, False, False)] = False
    ignore_name: Annotated[bool, flag("Files will not be compared by stem nor suffix.")] = False
    ignore_date: Annotated[bool, flag("If `media_magic=False`, files will not be compared by date.")] = False
    ignore_size: Annotated[bool, flag("If `media_magic=False`, files will not be compared by size.")] = False
    space2char: Annotated[bool, flag(
        """When comparing files in work_dir, consider space as another char. Ex: "file 012.jpg" is compared as "file_012.jpg" """)] = False
    strip_end_counter: Annotated[bool, flag(
        """When comparing files in work_dir, strip the counter. Ex: "00034(3).MTS" is compared as "00034.MTS" """)] = False
    strip_suffix: Annotated[str, opt(
        """When comparing files in work_dir, strip the file name end matched by a regular. Ex: "001-edited.jpg" is compared as "001.jpg" """, False)] = False
    work_file_stem_shortened: Annotated[int, opt(
        "Photos downloaded from Google have its stem shortened to 47 chars. For the comparing purpose, treat original folder file names shortened.", None)] = None
    invert_selection: Annotated[bool, flag(
        "Match only those files from work_dir that does not match the criterions.")] = False

    # Media section
    media_magic: Annotated[bool, flag(
        """Nor the size or date is compared for files with media suffixes.
    A video is considered a duplicate if it has the same name and a similar number of frames, even if it has a different extension.
    An image is considered a duplicate if it has the same name and a similar image hash, even if the files are of different sizes.
    (This mode is considerably slower.)
    """)] = False
    accepted_frame_delta: Annotated[int, opt(
        "Used only when media_magic is True", 1)] = 1
    accepted_img_hash_diff: Annotated[int, opt(
        "Used only when media_magic is True", 1)] = 1
    img_compare_date: Annotated[bool, flag(
        "If True and `media_magic=True`, the work file date or the work file EXIF date must match the original file date (has to be no more than an hour around).")] = False

    # Helper section
    log_level: Annotated[int, opt("10 debug .. 50 critical", logging.WARNING, 1)] = logging.WARNING
    output: Annotated[bool, flag(
        "Stores the output log to a file in the current working directory. (Never overwrites an older file.)")] = False

    # TODO bashize should be outputtable through output

    # Following parameters are undocumented:

    file_list: list[Path] = None
    "Use original file list. If none, a new is generated or a cached version is used."
    suffixes: bool | tuple[str] = False
    "If set, only files with such suffixes are compared. Ex: `suffixes = MEDIA_SUFFIXES`"

    skip: int = 0
    "Skip first n files in work_dir. Useful when a big task is interrupted and we want to continue without checking again the first part that has already been processed."

    debug: bool = None
    fail_on_error: bool = False
    shorter_log: bool = True
    "TODO deprecated If True, common prefix of the file names are not output to the log to save space."

    ending_counter = re.compile(r"\(\d+\)$")

    def __repr__(self):
        text = ', '.join(f'{attr}={len(v)  if isinstance(v, (set, list, dict)) else v}' for attr,
                         v in vars(self).items())
        return f'Deduplidog({text})'

    def __post_init__(self):
        logging.basicConfig(level=self.log_level, format="%(message)s", force=True)
        logger.setLevel(self.log_level)
        [handler.setLevel(self.log_level) for handler in logger.handlers]

        self.changes: list[Change] = []
        "Path to the files to be changed and path to the original file and status"
        self.passed_away: set[Path] = set()
        "These paths were renamed etc."
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
        self.bar: tqdm | None = None
        "Work files iterator"
        self._files_cache: dict[str, set[Path]] = defaultdict(set)
        "Original files, grouped by stem"
        self.metadata: dict[Path, FileMetadata] = keydefaultdict(FileMetadata)
        "File metadata like stat() (which is not cached by default)"
        self._common_prefix_length = 0
        " TODO deprecated"
        self.original_dir_name = self.work_dir_name = None
        "Shortened name, human readable"
        self.same_superdir = False
        """ Work_dir and original dir is the same """
        self._output = None
        " Log buffer "

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
        if not self.ignore_name:
            for p in self.file_list:
                p_case = Path(str(p).casefold()) if self.casefold else p
                self._files_cache[p_case.stem[:self.work_file_stem_shortened]].add(p)
        elif self.media_magic:
            # We preload the metadata cache, since we think there will be a lot of candidates.
            # This is because media_magic does not use date nor size file filtering so evaluating the first work_file might
            # take ages. Here, we put a nice progress bar.
            self.preload_metadata(self.file_list)

        self._common_prefix_length = len(os.path.commonprefix([self.original_dir, self.work_dir])) \
            if self.shorter_log else 0

        if self.output:
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
                print(f"{'Affected' if self.execute else 'Affectable'}:"
                      f" {self.affected_count}/{len(self.file_list)- self.ignored_count}", end="")
                if self.ignored_count:
                    print(f" ({self.ignored_count} ignored)", end="")
                print("\nAffected size:", naturalsize(self.size_affected))
                if self.warning_count:
                    print(f"Warnings: {self.warning_count}")
                if self.having_multiple_candidates:
                    print("Unsuccessful files having multiple candidates length:", len(self.having_multiple_candidates))

    def preload_metadata(self, files: list[Path]):
        """ Populate self.metadata with performance-intensive file information """
        # Strangely, when I removed cached_properties from FileMetadata in order to be serializable for multiprocesing,
        # using ThreadPoolExecutor is just as quick as ProcessPoolExecutor. And it spans multiple processes too.
        # I thought ThreadPoolExecutor spans just threads.
        images = [x for x in files if x.suffix.lower() in IMAGE_SUFFIXES]
        with ProcessPoolExecutor() as executor:
            for file, *args in tqdm(executor.map(FileMetadata.preload, images),
                                    total=len(images), desc="Caching image hashes"):
                self.metadata[file] = FileMetadata(file, *args)

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

        if self.skip_bigger and not self.media_magic:
            raise AssertionError("The skip_bigger works only with media_magic")

        if self.invert_selection and any((self.replace_with_original, self.treat_bigger_as_original, self.set_both_to_older_date)):
            raise AssertionError(
                "It does not make sense using invert_selection with this command. The work file has no file to compare to.")

        match self.tolerate_hour:
            case True:
                self.tolerate_hour = -1, 1
            case n if isinstance(n, int):
                self.tolerate_hour = -abs(n), abs(n)
            case n if isinstance(n, tuple) and all(isinstance(x, int) for x in n):
                pass
            case _:
                raise AssertionError("Use whole hours only")

        if self.ignore_name and self.ignore_date and self.ignore_size:
            raise AssertionError("You cannot ignore everything.")

        if self.media_magic:
            print("Only files with media suffixes are taken into consideration."
                  f" Nor the size nor the date is compared.{' Nor the name!' if self.ignore_name else ''}")
        else:
            if self.ignore_size and self.checksum:
                raise AssertionError("Checksum cannot be counted when ignore_size.")
            used, ignored = (", ".join(filter(None, x)) for x in zip(
                self.ignore_name and ("", "name") or ("name", ""),
                self.ignore_size and ("", "size") or ("size", ""),
                self.ignore_date and ("", "date") or ("date", ""),
                self.checksum and ("crc32", "") or ("", "crc32")))
            print(f"Find files by {used}{f', ignoring: {ignored}' if ignored else ''}")

        dirs_ = "" if self.same_superdir else f" at '{self.work_dir_name}' or the original dir at '{self.original_dir_name}'"
        which = f"either the file from the work dir{dirs_} (whichever is bigger)" \
            if self.treat_bigger_as_original \
            else f"duplicates from the work dir at '{self.work_dir_name}'"
        small = " (only if smaller than the pair file)" if self.skip_bigger else ""
        nonzero = " with non-zero size" if self.skip_empty else ""
        action = "will be" if self.execute else f"would be (if execute were True)"
        print(f"{which.capitalize()}{small}{nonzero} {action} ", end="")

        match self.rename, self.replace_with_original, self.delete:
            case False, False, False:
                pass
            case True, False, False:
                print("renamed (prefixed with ✓).")
            case False, True, False:
                print("replaced with the original.")
            case False, False, True:
                print("deleted.")
            case _:
                raise AssertionError("Choose either rename or replace_with_original")

        if self.set_both_to_older_date:
            print("Original file mtime date might be set backwards to the duplicate file.")
        print("")  # sometimes, this line is consumed

    def _loop_files(self):
        work_dir, skip = self.work_dir, self.skip
        work_files = [f for f in tqdm((p for p in Path(work_dir).rglob(
            "*") if not p.is_dir()), desc="Caching working files")]
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
                        print("Repeating on exception", work_file, e)
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
        if self.space2char:
            stem = stem.replace(" ", self.space2char)
        if self.strip_end_counter:
            stem = self.ending_counter.sub("", stem)
        if self.strip_suffix:
            stem = re.sub(self.strip_suffix + "$", "", stem)
        if self.casefold:
            stem = stem.casefold()

        if work_file.is_symlink() or self.suffixes and work_file.suffix.lower() not in self.suffixes:
            logger.debug("Skipping symlink or a non-wanted suffix: %s", work_file)
            return
        if self.skip_empty and not work_file.stat().st_size:
            logger.debug("Skipping zero size: %s", work_file)
            return

        # print stats
        bar.set_postfix({"size": naturalsize(self.size_affected),
                         "affected": self.affected_count,
                         "file": str(work_file)[len(str(self.work_dir)):]
                         })

        # candidate = name matches
        _candidates_fact = (p for p in (self.file_list if self.ignore_name else self._files_cache[stem]) if
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
        if original and not self.invert_selection:
            self._affect(work_file, original)
        elif not original and self.invert_selection:
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
        if self.media_magic:  # why checking media_magic?
            # This is just a double check because if not media_magic,
            # the files must have the same size nevertheless.)
            work_size, orig_size = work_file.stat().st_size, original.stat().st_size
            match self.treat_bigger_as_original, work_size > orig_size:
                case True, True:
                    affected_file, other_file = original, work_file
                case False, True:
                    change[work_file].append(f"SIZE WARNING {naturalsize(work_size-orig_size)}")
                    warning = work_file
            if self.skip_bigger and affected_file.stat().st_size > other_file.stat().st_size:
                logger.debug("Skipping %s as it is not smaller than %s", affected_file, other_file)
                return

        # execute changes or write a log

        # setting date
        affected_date, other_date = affected_file.stat().st_mtime, other_file.stat().st_mtime
        match self.set_both_to_older_date, affected_date != other_date:
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

        if warning and not self.neglect_warning:
            change[warning].append("🛟skipped on warning")
        else:
            self.size_affected += affected_file.stat().st_size
            self.affected_count += 1

            # other actions
            if self.rename:
                self._rename(change, affected_file)

            if self.delete:
                self._delete(change, affected_file)

            if self.replace_with_original:
                self._replace_with_original(change, affected_file, other_file)

        self.changes.append(change)
        if warning:
            self.warning_count += 1
        if (warning and self.log_level <= logging.WARNING) or (self.log_level <= logging.INFO):
            self.bar.clear()  # this looks the same from jupyter and much better from terminal (does not leave a trace of abandoned bars)
            self._print_change(change)
        if self._output:
            with redirect_stdout(self._output):
                self._print_change(change)

    def _rename(self, change: Change, affected_file: Path):
        msg = "renamable"
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
                    msg = "renaming"
            if self.bashify:
                print(f"mv -n {_qp(affected_file)} {_qp(target_path)}")
            self.passed_away.add(affected_file)
            self.metadata.pop(affected_file, None)
        change[affected_file].append(msg)

    def _delete(self, change: Change, affected_file: Path):
        msg = "deletable"
        if self.execute or self.bashify:
            if self.execute:
                affected_file.unlink()
                msg = "deleting"
            if self.bashify:
                print(f"rm {_qp(affected_file)}")
            self.passed_away.add(affected_file)
            self.metadata.pop(affected_file, None)
        change[affected_file].append(msg)

    def _replace_with_original(self, change: Change, affected_file: Path, other_file: Path):
        msg = "replacable"
        if other_file.name == affected_file.name:
            if self.execute:
                msg = "replacing"
                shutil.copy2(other_file, affected_file)
            if self.bashify:
                print(f"cp --preserve {_qp(other_file)} {_qp(affected_file)}")  # TODO check
        else:
            if self.execute:
                msg = "replacing"
                shutil.copy2(other_file, affected_file.parent)
                affected_file.unlink()
            if self.bashify:
                # TODO check
                print(f"cp --preserve {_qp(other_file)} {_qp(affected_file.parent)} && rm {_qp(affected_file)}")
        change[affected_file].append(msg)
        self.metadata.pop(affected_file, None)

    def _change_file_date(self, path, old_date, new_date, change: Change):
        # Consider following usecase:
        # Duplicated file 1, date 14:06
        # Duplicated file 2, date 15:06
        # Original file,     date 18:00.
        # The status message will mistakingly tell that we change Original date to 14:06 (good), then to 15:06 (bad).
        # However, these are just the status messages. But as we resolve the dates at the launch time,
        # original date will end up as 14:06 because 15:06 will be later.
        change[path].extend(("redating" if self.execute else 'redatable',
                            datetime.fromtimestamp(old_date), "->", datetime.fromtimestamp(new_date)))
        if self.execute:
            os.utime(path, (new_date,)*2)  # change access time, modification time
            self.metadata.pop(path, None)
        if self.bashify:
            print(f"touch -t {new_date} {_qp(path)}")  # TODO check

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
            if (self.ignore_date
                    or wst.st_mtime == ost.st_mtime
                    or self.tolerate_hour and self.tolerate_hour[0] <= (wst.st_mtime - ost.st_mtime)/3600 <= self.tolerate_hour[1]
                    ) and (self.ignore_size or wst.st_size == ost.st_size and (not self.checksum or crc(original) == crc(work_file))):
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
                similar = frame_delta <= self.accepted_frame_delta
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
            if self.img_compare_date:
                exif_times = orig_cache.exif_times
                file_time = orig_cache.stat.st_mtime
                ref_time = work_cache.stat.st_mtime
                similar = abs(ref_time - file_time) <= 3600 \
                    or any(abs(ref_time - t) <= 3600 for t in exif_times)

            if similar or not self.img_compare_date:
                hash0 = orig_cache.average_hash
                hash1 = work_cache.average_hash
                # maximum bits that could be different between the hashes
                hash_dist = abs(hash0 - hash1)
                similar = hash_dist <= self.accepted_img_hash_diff
                if not similar and self.debug:
                    print("Hash distance:", hash_dist)
            return similar
        except OSError as e:
            logger.error("OSError %s %s %s", e, orig_cache.file, work_cache.file)
        finally:
            orig_cache.clean()

    @staticmethod
    @cache
    def build_originals(original_dir: str | Path, suffixes: bool | tuple[str]):
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

        print("*", wf)
        print(" ", of)
        [print(text, *(str(s) for s in changes))
            for text, changes in zip((f"  {wicon}{wn}:",
                                      f"  {oicon}{on}:"), change.values()) if len(changes)]
