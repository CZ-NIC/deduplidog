from datetime import datetime
import os
from pathlib import Path
import re
from humanize import naturalsize
from tqdm.notebook import tqdm
import imagehash
from PIL import Image, ExifTags
from functools import cache
from time import sleep
import ipdb
import pendulum
from threading import Thread
from queue import Queue


VIDEO_SUFFIXES = (".mp4", ".mov", ".avi", ".vob", ".mts",
                  ".3gp", ".mpg", ".mpeg", ".wmv")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif")
MEDIA_SUFFIXES = IMAGE_SUFFIXES + VIDEO_SUFFIXES


class MediaDuplicateFinder():

    ending_counter = re.compile(r"\(\d+\)$")

    def __init__(self, work_dir: str, originals: str,
                 execute=False, rename=True, set_both_to_older_date=False,
                 treat_bigger_as_original=False,
                 space2char=False, strip_end_counter=False, strip_suffix=False,  work_file_stem_shortened=None,
                 accepted_frame_delta=1, accepted_img_hash_diff=1, img_compare_date=False,
                 file_list: list[Path]=None, suffixes=MEDIA_SUFFIXES,
                 skip: int = 0,
                 debug=None, fail_on_error=False, shorter_log=True,
                 ):
        """
        Najde duplikáty.
        Za duplikát videa se považuje, pokud má stejné jméno a podobný počet framů a třeba jinou koncovku.
        Za duplikát obrázku se považuje, pokud má stejné jméno a podobný image hash (even if the files are of another size).

        img_compare_date: If True, aby se obrázek považoval za duplikát, musí mít podobný čas v EXIFu či souboru

        work_dir: Folder of the files suspectible to be duplicates.
        originals: Folder of the original files. Normally, these files will not be affected.
            (However, they might get affected by treat_bigger_as_original or set_both_to_older_date).

        execute: If false, nothing happens.
        rename: If execute=True, prepend ✓ to the file name of a duplicate file (in the work_dir, if treat_bigger_as_original is not set)
        set_both_to_older_date: If execute=True, both files are set to the older date. Ex: work file get's the original file's date or vice versa.

        treat_bigger_as_original: If True, the original file might be affected (by renaming) if smaller than the work file.

        strip_end_counter: If True, files work_dir are stripped the counter. Ex: "00034(3).MTS" is compared as "00034.MTS"
        strip_suffix: str If regular str, files in work_dir suffix is stripped. Ex: "001-edited.jpg" is compared as "001.jpg"
        work_file_stem_shortened: int Photos downloaded from Google have its stem shortened to 47 chars. For the comparing purpose, treat original folder file names shortened.

        file_list: Use original file list. If not, a new is generated or a cached version is used.
        skip: int Skip first n files in work_dir. Useful when a big task is interrupted and we want to continue without checking again the first part that has already been processed.

        shorter_log: If True, common prefix of the file names are not output to the log to save space.

        """
        self.size_affected = 0
        " stats counter "
        self.affected_count = 0
        " stats counter "
        self.having_multiple_candidates = []
        self.debug = debug
        self.work_file_stem_shortened = work_file_stem_shortened
        self.fail_on_error = fail_on_error
        self.suffixes = suffixes
        self.rename = rename
        self.execute = execute
        self.accepted_frame_delta = accepted_frame_delta
        self.accepted_img_hash_diff = accepted_img_hash_diff
        self.strip_end_counter = strip_end_counter
        self.strip_suffix = strip_suffix
        self.set_both_to_older_date = set_both_to_older_date
        self.treat_bigger_as_original = treat_bigger_as_original
        self.img_compare_date = img_compare_date

        # build file list of the originals
        if file_list:
            if not str(file_list[0]).startswith(originals):
                print(
                    "Fail: We received cached file_list but it seems containing other directory than originals.")
                return
        else:
            file_list = MediaDuplicateFinder.build_originals(originals)
        self.file_list = file_list
        print("Number of originals:", len(file_list))
        self.common_prefix_length = len(os.path.commonprefix([originals, work_dir])) if shorter_log else 0

        # loop all files in the work dir and check them for duplicates amongst originals
        # try#
        # concurrent worker to rename files
        # we suppose this might be quicker than waiting the renaming IO action is done
        # BUT IT IS NOT AT ALL
        # self.queue = Queue()
        # worker = Thread(target=self._rename_worker, args=(self.queue,))
        # worker.start()

        self._loop_files(work_dir, space2char, file_list, skip)
        # finally:
        #    self.queue.put(None)
        #    worker.join()
        #    print("Worker finished")

        print("Size:", naturalsize(self.size_affected))
        print("having_multiple_candidates len:",
              len(self.having_multiple_candidates))

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

    def _loop_files(self, work_dir: str, space2char: str, file_list: list[Path], skip: bool):

        work_files = [f for f in Path(work_dir).rglob("*")]
        if skip:
            if isinstance(work_files, list):
                work_files = work_files[skip:]
            else:
                [next(work_files) for _ in range(skip)]
            print("Skipped", skip)
        # a = 0
        for work_file in (bar := tqdm(work_files)):
            for attempt in range(5):
                try:
                    self._process_file(work_dir, space2char, file_list,  work_file, bar)
                except Image.DecompressionBombError as e:
                    print("Failing on exception", work_file, e)
                except Exception as e:
                    sleep(1 * attempt)
                    print("Repeating on exception", work_file, e)
                    if self.fail_on_error:
                        raise
                    else:
                        continue
                except KeyboardInterrupt:
                    print(f"Interrupted. You may proceed where you left with the skip={skip+bar.n} parameter.")
                    return
                break

    def _process_file(self, work_dir: str, space2char: str, file_list: list[Path], work_file: Path, bar: tqdm):
        # work file name transformation
        name = str(work_file.name)
        if name.startswith("✓"):
            return
        stem = str(work_file.stem)
        if space2char:
            stem = stem.replace(" ", space2char)
        if self.strip_end_counter:
            stem = self.ending_counter.sub("", stem)
        if self.strip_suffix:
            stem = re.sub(self.strip_suffix + "$", "", stem)

        if work_file.is_symlink() or work_file.suffix.lower() not in self.suffixes:
            return

        # print stats
        bar.set_postfix({"size": naturalsize(self.size_affected),
                         "affected": self.affected_count,
                         "file": str(work_file)[len(work_dir):]
                         })

        # build a candidate list
        comparing_image = work_file.suffix.lower() in IMAGE_SUFFIXES
        # comparing_video = work_file.suffix.lower() in VIDEO_SUFFIXES
        candidates = [x for x in file_list if
                      stem == x.stem[:self.work_file_stem_shortened]
                      # comparing images to images and videos to videos
                      and x.suffix.lower() in (IMAGE_SUFFIXES if comparing_image else VIDEO_SUFFIXES)]

        # check candidates
        original = self._find_similar(work_file, comparing_image, candidates)

        # original of the work_file has been found
        # one of them might be treated as a duplicate and thus affected
        if original:
            self._affect(work_file, original)
        elif len(candidates) > 1:  # we did not find the object amongst multiple candidates
            self.having_multiple_candidates.append(work_file)

    def _affect(self, work_file: Path, original: Path):
        # which file will be affected? The work file or the mistakenly original file?
        status = {work_file: [], original: []}
        work_size, orig_size = work_file.stat().st_size, original.stat().st_size
        match self.treat_bigger_as_original, work_size > orig_size:
            case (True, True):
                affected_file, other_file = original, work_file
            case (False, True):
                status[work_file].append(f"SIZE WARNING {naturalsize(work_size-orig_size)}")
                affected_file, other_file = work_file, original
            case _:
                affected_file, other_file = work_file, original

        # execute changes or write a log
        self.size_affected += affected_file.stat().st_size
        self.affected_count += 1

        # setting date
        affected_date, other_date = affected_file.stat().st_mtime, other_file.stat().st_mtime
        match self.set_both_to_older_date, affected_date != other_date:
            case (True, True):
                # dates are not the same and we want change them
                if other_date < affected_date:
                    self._change_file_date(affected_file, affected_date, other_date, status)
                elif other_date > affected_date:
                    self._change_file_date(other_file, other_date, affected_date, status)
            case (False, True) if (other_date > affected_date):
                # attention, we do not want to tamper dates however the file marked as duplicate has
                # lower timestamp (which might be genuine)
                status[other_file].append(f"DATE WARNING (lower)")

        # renaming
        if self.rename:
            if self.execute:
                # self.queue.put((affected_file, affected_file.with_name("✓" + affected_file.name)))
                affected_file.rename(affected_file.with_name("✓" + affected_file.name))
                status[affected_file].append("renaming")
            else:
                status[affected_file].append("renamable")

        if affected_file == work_file:
            print("Original:", self._path(original), *status[original])
            print("Work file:", self._path(work_file), *status[work_file])
        elif affected_file == original:
            print("Original (affected):", self._path(original), *status[original])
            print("Work file:", self._path(work_file), *status[work_file])

    def _change_file_date(self, path, old_date, new_date, status):
        status[path].extend(("redating" if self.execute else 'redatable',
                            datetime.fromtimestamp(old_date), "->", datetime.fromtimestamp(new_date)))
        if self.execute:
            os.utime(path, (new_date,)*2)  # change access time, modification time

    def _path(self, path):
        """ Strips out common prefix that has originals with work_dir for display reasons.
            /media/user/disk1/Photos -> 1/Photos
            /media/user/disk2/Photos -> 2/Photos
        """
        return str(path)[self.common_prefix_length:]

    def _find_similar(self,  work_file: Path, comparing_image: bool, candidates: list[Path]):
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
    def build_originals(originals: str):
        return [x for x in tqdm(Path(originals).rglob("*"), desc="Caching original files") if not x.is_symlink() and x.suffix.lower() in MEDIA_SUFFIXES]



def get_frame_count(filename):
    import cv2
    video = cv2.VideoCapture(str(filename))


    #duration = video.get(cv2.CAP_PROP_POS_MSEC)
    frame_count = video.get(cv2.CAP_PROP_FRAME_COUNT)

    return frame_count


def mark_symlink_by_target(suspicious_directory: str|Path, starting_path):
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

from sh import find
import os
from IPython.display import clear_output


def _stub():
    while True:
        a = input()
        clear_output()
        cwd = "/media/user/disk1/Photos/"
        print("Searching", a,"in", cwd)
        files = find("-iname", f"*{a}*", _cwd=cwd)
        files = [Path(cwd, f.strip()) for f in files]
        print("Len", len(files))
        images(files)
        [print_video_thumbs(f) for f in files]


from itertools import chain
def _are_similar(original:Path, work_file:Path, accepted_img_hash_diff:int=1):
    original_pil = Image.open(original)
    work_pil = Image.open(work_file)
    hash0 = imagehash.average_hash(original_pil)
    hash1 = imagehash.average_hash(work_pil)
    # maximum bits that could be different between the hashes
    return abs(hash0 - hash1) <= accepted_img_hash_diff

def are_contained(work_dir, original_dir, sec_range:int=60):
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
        range_ = sorted(range(-sec_range, sec_range+1), key=lambda x: abs(x))  # 0, -1, 1, -2, 2 ... to find candidate earlier
        corresponding = (originals.get(timestamp + i, set()) for i in range_)  # find all originals with similar timestamps
         # flatten the sets and unique them (but keep as list to preserve files with less timestamp difference first)
        corresponding = list(dict.fromkeys(chain.from_iterable(corresponding)))

        if corresponding:
            for candidate in (bar2:=tqdm(corresponding, leave=False, desc="Candidates")):
                bar2.set_postfix({"file": candidate.name})
                if _are_similar(candidate, wf):
                    found[wf] = candidate
                    bar2.update(float("inf"))  # tqdm would not dissappear if not finished https://github.com/tqdm/tqdm/issues/1382
                    bar2.close()
                    break
            else:
                print("No candidate for", wf.name, corresponding)
                images([wf] + list(corresponding))
        else:
            print("Missing originals for", wf.name)


#are_contained("/media/user/disk1/Photos/_tabor/2/", "/media/user/disk1/Photos/tabory/C 074 2016/")

import cv2
from IPython.display import Image, display
from ipywidgets import widgets, HBox

def print_video_thumbs(src):
    vidcap = cv2.VideoCapture(str(src))
    success,image = vidcap.read()
    count = 0
    images = []
    while success:
        success,image = vidcap.read()
        if count % 100 == 0:
            try:
                #images.append(Image(width=150, data=cv2.imencode('.jpg', image)[1]))
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


#mark_symlink_only_dirs("/media/user/disk2/Takeoutuser/Google Photos/")


def mark_01_copies(suspicious_directory):
    for f in (x for x in Path(suspicious_directory).glob("*(1)*")):
        stem = f.stem.removesuffix("(1)")

        for x in (x for x in Path("/media/user/disk2/_duplikaty_smazat/").rglob("*") if x.stem.removeprefix("✓") == stem):
            print(f.rename(f.with_name("→" + f.name)))
#mark_01_copies("/media/user/disk2/Takeoutuser/YouTube and YouTube Music/videos/")

import json
import os
from datetime import datetime

def mtime_files_in_dir_according_to_json(dir_, json_dir):
    """ google photos vrací json, kde je čas fotky
    Kromě JPG.
    """
    for photo in Path(dir_).rglob("*"):
        #if photo.suffix.lower() in (".jpg", ".jpeg"):
        #    continue
        #if "50607264_2240519186012556_9095104762705084416_o.jpg" not in photo.name:
        #    continue
        metadata = Path(json_dir).joinpath(photo.name[:46]  + ".json")
        if metadata.exists():
            #if photo.stat().st_mtime < 1654812000:
                # zmenit jenom takove soubory, ktere uz nebyly zmeneny jinak,
                # coz poznam tak, ze jejich datum je 10.6.2022
            #    continue
            timestamp = json.loads(metadata.read_text())["photoTakenTime"]["timestamp"]
            os.utime(photo, (int(timestamp), int(timestamp)))
            print(photo)
            #break

#mtime_files_in_dir_according_to_json("/media/user/disk2/Takeoutuser/Google Photos/Photos from 2019/",
                                    #"/media/user/disk2/photos_json/")



# launch

mdf = MediaDuplicateFinder(
    "/media/user/disk1/_DUPLIK/Young Hacker/",
    originals="/media/user/disk1/Vault/_netrizene/ROZTRIDIT/zzz_moznaDuplik/",
    space2char="_",
    strip_end_counter=True,
    accepted_frame_delta=5,
    accepted_img_hash_diff=3,
    work_file_stem_shortened=47,
    shorter_log=False,
    execute=False,
    rename=True,
    fail_on_error=True,
)

# DISK1_PHOTOS = mdf.file_list
# NAHRAVKY_LIST = mdf.file_list
