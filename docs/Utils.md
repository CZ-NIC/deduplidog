## Utils

The library might be invoked from a [Jupyter Notebook](https://jupyter.org/).

```python3
from deduplidog import Deduplidog
Deduplidog("/home/user/duplicates", "/media/disk/origs", ignore_date=True, rename=True).start()
```

In the `deduplidog.utils` packages, you'll find a several handsome tools to help you. You will find parameters by using your IDE hints.

### `images`
*`urls: Iterable[str | Path]`* Display a ribbon of images.

### `print_video_thumbs`
*`src: str | Path`* Displays thumbnails for a video.

### `print_videos_thumbs`
*`dir_: Path`* To quickly understand the content of each video, output the duration and the first few frames.

### `get_frame_count`
*`filename: str|Path`* Uses cv2 to determine the video frame count. Method is cached.

### `search_for_media_wizzard`
*`cwd: str`* Repeatedly prompt and search for files with similar names somewhere in the specified path. Display all such files as images and video previews.

### `are_contained`
*`work_dir: str, original_dir: str, sec_range: int = 60`*  You got two dirs with files having different naming system (427.JPG vs DSC_1344)
        which you suspect to contain the same set. The same files in the dirs seem to have the same timestamp.
        The same timestamp means +/- sec_range (ex: 1 minute).
        Loop all files from work_dir and display corresponding files having the same timestamp.
        or warn that no original exists.

### `remove_prefix_in_workdir`
*`work_dir: str`* Removes the prefix ✓ recursively from all the files. The prefix might have been previously given by the deduplidog.


### `mark_symlink_by_target`
*`suspicious_directory: str | Path, starting_path: str`* If the file is a symlink, pointing to this path, rename it with an arrow.

```
:param suspicious_directory: Ex: /media/user/disk/Takeout/Photos/
:param starting_path: Ex: /media/user/disk
```

### `mark_symlink_only_dirs`
*`dir_: str | Path`* If the directory is full of only symlinks or empty, rename it to an arrow.

### `mtime_files_in_dir_according_to_json`
*`dir_: str | Path, json_dir: str | Path`*  Google Photos returns JSON with the photo modification time. Sets the photos from the dir_ to the dates fetched from the directory with  these JSONs.
