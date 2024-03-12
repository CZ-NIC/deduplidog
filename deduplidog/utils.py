import json
import os
from collections import defaultdict
from functools import cache
from itertools import chain
from pathlib import Path
from typing import Iterable
from zlib import crc32

import cv2
import imagehash
from IPython.display import clear_output, display
from ipywidgets import HBox, widgets
from PIL import Image
from sh import find
from tqdm.autonotebook import tqdm


@cache
def crc(path: Path):  # undocumented function
    """ Count CRC32 file hash.
    Surprisingly, sha256 and sha1 was faster than md5 when using hashlib.file_digest. However crc32 is still the fastest."""
    crc = 0
    with path.open('rb') as f:
        while True:
            chunk = f.read(4096)
            if not chunk:
                break
            crc = crc32(chunk, crc)
    return crc


def _qp(path: Path):
    """Quoted path. Output path to be used in bash. I wonder there is no system method which covers
    quotes in the path etc.
    """
    s = str(path)
    return f'"{s}"' if " " in s else s


def images(urls: Iterable[str | Path]):
    """ Display a ribbon of images. """
    images_ = []
    for url in tqdm(urls, leave=False):
        p = Path(url)
        if p.exists():
            images_.append(widgets.Image(width=150, value=p.read_bytes()))
        else:
            print("Fail", p)
    display(HBox(images_))


def print_video_thumbs(src: str | Path):
    """ Displays thumbnails for a video """
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


def print_videos_thumbs(dir_: Path):
    """ To quickly understand the content of each video, output the duration and the first few frames. """
    for f in sorted(Path(dir_).rglob("*")):
        if f.suffix.lower() in (".mov", ".avi", ".mp4", ".vob"):
            print_video_thumbs(f)


@cache
def get_frame_count(filename: str | Path):
    """ Uses cv2 to determine the video frame count. Method is cached."""
    video = cv2.VideoCapture(str(filename))
    # duration = video.get(cv2.CAP_PROP_POS_MSEC)
    frame_count = video.get(cv2.CAP_PROP_FRAME_COUNT)
    return frame_count


def search_for_media_wizzard(cwd: str):
    """ Repeatedly prompt and search for files with similar names somewhere in the specified path.
    Display all such files as images and video previews. """
    while True:
        query = input()
        clear_output()
        print("Searching", query, "in", cwd)
        files = find("-iname", f"*{query}*", _cwd=cwd)
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


def are_contained(work_dir: str, original_dir: str, sec_range: int = 60):
    """ You got two dirs with files having different naming system (427.JPG vs DSC_1344)
        which you suspect to contain the same set. The same files in the dirs seem to have the same timestamp.
        The same timestamp means +/- sec_range (ex: 1 minute).
        Loop all files from work_dir and display corresponding files having the same timestamp.
        or warn that no original exists.
        """

    # build directory of originals
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


def remove_prefix_in_workdir(work_dir: str):
    """ Removes the prefix ✓ recursively from all the files.
    The prefix might have been previously given by the deduplidog. """
    work_files = [f for f in tqdm(Path(work_dir).rglob("*"), desc="Caching working files") if f.is_file()]
    for p in work_files:
        p.rename(p.with_stem(p.stem.removeprefix("✓")))


def mark_symlink_by_target(suspicious_directory: str | Path, starting_path: str):
    """ If the file is a symlink, pointing to this path, rename it with an arrow

    :param suspicious_directory: Ex: /media/user/disk/Takeout/Photos/
    :param starting_path: Ex: /media/user/disk
    """
    for f in (x for x in Path(suspicious_directory).rglob("*") if x.is_symlink()):
        if str(f.resolve()).startswith(starting_path):
            print(f.rename(f.with_name("→" + f.name)))
            print(f)


def mark_symlink_only_dirs(dir_: str | Path):
    """If the directory is full of only symlinks or empty, rename it to an arrow."""
    for d in (x for x in Path(dir_).rglob("*") if x.is_dir()):
        if all(x.is_symlink() for x in Path(d).glob("*")):
            print(d.rename(d.with_name("→" + d.name)))


def mtime_files_in_dir_according_to_json(dir_: str | Path, json_dir: str | Path):
    """ Google Photos returns JSON with the photo modification time.
    Sets the photos from the dir_ to the dates fetched from the directory with  these JSONs.
    """
    for photo in Path(dir_).rglob("*"):
        metadata = Path(json_dir).joinpath(photo.name[:46] + ".json")
        if metadata.exists():
            timestamp = json.loads(metadata.read_text())["photoTakenTime"]["timestamp"]
            os.utime(photo, (int(timestamp), int(timestamp)))
            print(photo)
