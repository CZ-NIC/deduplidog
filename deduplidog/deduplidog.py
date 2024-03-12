import logging
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from time import sleep
from typing import Annotated, get_args, get_type_hints

import click
import imagehash
from dataclass_click import option
from humanize import naturaldelta, naturalsize
from PIL import ExifTags, Image
from tqdm.autonotebook import tqdm

from .interface_utils import Field
from .utils import _qp, crc, get_frame_count

VIDEO_SUFFIXES = ".mp4", ".mov", ".avi", ".vob", ".mts", ".3gp", ".mpg", ".mpeg", ".wmv"
IMAGE_SUFFIXES = ".jpg", ".jpeg", ".png", ".gif"
MEDIA_SUFFIXES = IMAGE_SUFFIXES + VIDEO_SUFFIXES

logger = logging.getLogger(__name__)
Change = dict[Path, list[str | datetime]]
"Lists changes performed/suggested to given path. First entry is the work file, the second is the original file."


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


def opt(help, default):
    "CLI support"
    return option(help=help, default=default, type=click.UNPROCESSED, callback=conversion)


@dataclass
class Deduplidog:
    """
    Find the duplicates.

    Normally, the file must have the same size, date and name. (Name might be just similar if parameters like strip_end_counter are set.)

    If media_magic=True, media files receive different rules: Neither the size nor the date are compared. See its help.
    """

    work_dir: Annotated[str | Path, option(
        help="""Folder of the files suspectible to be duplicates.""", required=True, type=click.UNPROCESSED)]
    original_dir: Annotated[str | Path, option(
        help="""Folder of the original files. Normally, these files will not be affected.
        (However, they might get affected by treat_bigger_as_original or set_both_to_older_date).""", default="", type=click.UNPROCESSED)] = ""

    # Action section
    execute: Annotated[bool, flag(
        "If False, nothing happens, just a safe run is performed.")] = False
    bashify: Annotated[bool, flag(
        """Print bash commands that correspond to the actions that would have been executed if execute were True.
     You can check and run them yourself.""")] = False
    affect_only_if_smaller: Annotated[bool, flag(
        """If media_magic=True, all writing actions like rename, replace_with_original, set_both_to_older_date and treat_bigger_as_original
     are executed only if the affectable file is smaller than the other.""")] = False
    rename: Annotated[bool, flag(
        """If execute=True, prepend ✓ to the duplicated work file name (or possibly to the original file name if treat_bigger_as_original).
     Mutually exclusive with replace_with_original and delete.""")] = False
    delete: Annotated[bool, flag(
        """If execute=True, delete theduplicated work file name (or possibly to the original file name if treat_bigger_as_original).
     Mutually exclusive with replace_with_original and rename.""")] = False
    replace_with_original: Annotated[bool, flag(
        """If execute=True, replace duplicated work file with the original (or possibly vice versa if treat_bigger_as_original).
    Mutually exclusive with rename and delete.""")] = False
    set_both_to_older_date: Annotated[bool, flag(
        "If execute=True, media_magic=True or (media_magic=False and ignore_date=True), both files are set to the older date. Ex: work file get's the original file's date or vice versa.")] = False
    treat_bigger_as_original: Annotated[bool, flag(
        "If execute=True and rename=True and media_magic=True, the original file might be affected (by renaming) if smaller than the work file.")] = False

    # Match section
    casefold: Annotated[bool, flag(
        "Case insensitive file name comparing.")] = False
    checksum: Annotated[bool, flag(
        """If media_magic=False and ignore_size=False, files will be compared by CRC32 checksum.
    (This mode is considerably slower.)""")] = False
    tolerate_hour: Annotated[int | tuple[int, int] | bool, opt(
        """When comparing files in work_dir and media_magic=False, tolerate hour difference.
        Sometimes when dealing with FS changes, files might got shifted few hours.
        * bool → -1 .. +1
        * int → -int .. +int
        * tuple → int1 .. int2
        Ex: tolerate_hour=2 → work_file.st_mtime -7200 ... + 7200 is compared to the original_file.st_mtime """, False)] = False
    ignore_date: Annotated[bool, flag(
        "If media_magic=False, files will not be compared by date.")] = False
    ignore_size: Annotated[bool, flag(
        "If media_magic=False, files will not be compared by size.")] = False
    space2char: Annotated[bool, flag(
        """When comparing files in work_dir, consider space as another char. Ex: "file 012.jpg" is compared as "file_012.jpg" """)] = False
    strip_end_counter: Annotated[bool, flag(
        """When comparing files in work_dir, strip the counter. Ex: "00034(3).MTS" is compared as "00034.MTS" """)] = False
    strip_suffix: Annotated[str, opt(
        """When comparing files in work_dir, strip the file name end matched by a regular. Ex: "001-edited.jpg" is compared as "001.jpg" """, False)] = False
    work_file_stem_shortened: Annotated[int, opt(
        "Photos downloaded from Google have its stem shortened to 47 chars. For the comparing purpose, treat original folder file names shortened.", None)] = None

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
        "If True and media_magic=True, the file date or the EXIF date must match.")] = False

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
    logging_level: int = logging.WARNING

    ending_counter = re.compile(r"\(\d+\)$")

    def __repr__(self):
        text = ', '.join(f'{attr}={len(v)  if isinstance(v, (set, list, dict)) else v}' for attr,
                         v in vars(self).items())
        return f'Deduplidog({text})'

    def __post_init__(self):
        logging.basicConfig(level=self.logging_level, format="%(message)s", force=True)
        logger.setLevel(self.logging_level)
        [handler.setLevel(self.logging_level) for handler in logger.handlers]

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
        match self.tolerate_hour:
            case True:
                self.tolerate_hour = -1, 1
            case n if isinstance(n, int):
                self.tolerate_hour = -abs(n), abs(n)
            case n if isinstance(n, tuple) and all(isinstance(x, int) for x in n):
                pass
            case _:
                raise AssertionError("Use whole hours only")
        self._files_cache: dict[str, set[Path]] = defaultdict(set)
        "Original files, grouped by stem"

        self._common_prefix_length = 0
        " TODO deprecated"

        # Distinguish paths
        if not self.original_dir:
            self.original_dir = self.work_dir
        if not self.work_dir:
            raise AssertionError("Missing work_dir")
        else:
            for a, b in zip(Path(self.work_dir).parts, Path(self.original_dir).parts):
                if a != b:
                    self.work_dir_name = a
                    self.original_dir_name = b
                    break
            else:
                self.work_dir_name = a
                self.original_dir_name = "(same superdir)"

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
                print(
                    f"{'Affected' if self.execute else 'Affectable'}: {self.affected_count}/{len(self.file_list)- self.ignored_count}", end="")
                if self.ignored_count:
                    print(f" ({self.ignored_count} ignored)", end="")
                print("\nAffected size:", naturalsize(self.size_affected))
                if self.warning_count:
                    print(f"Warnings: {self.warning_count}")
                if self.having_multiple_candidates:
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
            if self.ignore_size and self.checksum:
                raise AssertionError("Checksum cannot be counted when ignore_size.")
            used, ignored = (", ".join(filter(None, x)) for x in zip(
                self.ignore_size and ("", "size") or ("size", ""),
                self.ignore_date and ("", "date") or ("date", ""),
                self.checksum and ("crc32", "") or ("", "crc32")))
            print(f"Find files by {used}{f', ignoring: {ignored}' if ignored else ''}")

        which = f"either the file from the work dir at '{self.work_dir_name}' or the original dir at '{self.original_dir_name}' (whichever is bigger)" \
            if self.treat_bigger_as_original \
            else f"duplicates from the work dir at '{self.work_dir_name}'"
        small = " (only if smaller than the pair file)" if self.affect_only_if_smaller else ""
        action = "will be" if self.execute else f"would be (if execute were True)"
        print(f"{which.capitalize()}{small} {action} ", end="")

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

    def _loop_files(self):
        work_dir, skip = self.work_dir, self.skip
        work_files = [f for f in tqdm(Path(work_dir).rglob("*"), desc="Caching working files")]
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
        change = {work_file: [], original: []}
        affected_file, other_file = work_file, original
        warning = False
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
                    warning = True
            if self.affect_only_if_smaller and affected_file.stat().st_size >= other_file.stat().st_size:
                logger.debug("Skipping %s as it is not smaller than %s", affected_file, other_file)
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
                    self._change_file_date(affected_file, affected_date, other_date, change)
                elif other_date > affected_date:
                    self._change_file_date(other_file, other_date, affected_date, change)
            case False, True if (other_date > affected_date):
                # attention, we do not want to tamper dates however the file marked as duplicate has
                # lower timestamp (which might be genuine)
                change[other_file].append(f"DATE WARNING + {naturaldelta(other_date-affected_date)}")
                warning = True

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
        if (warning and self.logging_level <= logging.WARNING) or (self.logging_level <= logging.INFO):
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
                frame_delta = abs(get_frame_count(work_file) - get_frame_count(original))
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
                hash_dist = abs(hash0 - hash1)
                similar = hash_dist <= self.accepted_img_hash_diff
                if not similar and self.debug:
                    print("Hash distance:", hash_dist)
            return similar
        except OSError as e:
            print(e, original, work_file)

    @staticmethod
    @cache
    def build_originals(original_dir: str | Path, suffixes: bool | tuple[str]):
        return [p for p in tqdm(Path(original_dir).rglob("*"), desc="Caching original files", leave=False) if p.is_file() and not p.is_symlink() and (not suffixes or p.suffix.lower() in suffixes)]

    def print_changes(self):
        "Prints performed/suggested changes to be inspected in a human readable form."
        [self._print_change(change) for change in self.changes]

    def _print_change(self, change: Change):
        wicon, oicon = "🔨", "📄"
        wf, of = change
        print("*", wf)
        print(" ", of)
        [print(text, *(str(s) for s in changes))
            for text, changes in zip((f"  {wicon}{self.work_dir_name}:",
                                      f"  {oicon}{self.original_dir_name}:"), change.values()) if len(changes)]

