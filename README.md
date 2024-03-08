[![Build Status](https://github.com/CZ-NIC/deduplidog/actions/workflows/run-unittest.yml/badge.svg)](https://github.com/CZ-NIC/deduplidog/actions)

Yet another file deduplicator.

# About

## What are the use cases?
* I have downloaded photos and videos from the cloud. Oh, both Google Photos and Youtube shrink the file and changes the format. Moreover, it have shortened the file name to 47 characters and capitalize the extension. So how should I know that I have them all backed up offline?
* My disk is cluttered with several backups and I'd like to be sure these are all just copies.
* I merge data from multiple sources. Some files in the backup might have the former orignal file modification date that I might wish to restore.

## What is compared?

* The file name.

Works great when the files keep more or less the same name. (Photos downloaded from Google have its stem shortened to 47 chars but that is enough.) Might ignore case sensitivity.

* The file date.

You can impose the same file *mtime*, tolerate few hours (to correct timezone confusion) or ignore the date altogether.

* The file size, the image hash or the video frame count.

The file must have the same size. Or take advantage of the media magic under the hood which ignores the file size but compares the image or the video inside. It is great whenever you end up with some files converted to a different format.

* The contents?

You may use `checksum=True` to perform CRC32 check. However for byte-to-byte checking, when the file names might differ or you need to check there is no byte corruption, some other tool might be better way, i.e. [jdupes](https://www.jdupes.com/).

## Why not using standard sync tools like [meld](https://meldmerge.org/)?
These imply the folders have the same structure. Deduplidog is tolerant towards files scattered around.

## Doubts?

The program does not write anything to the disk, unless `execute=True` is set. Feel free to launch it just to inspect the recommended actions. Or set `bashify=True` to output bash commands you may launch after thorough examining.

# Examples

It works great when launched from a [Jupyter Notebook](https://jupyter.org/).

```python3
import logging
from deduplidog import Deduplidog

Deduplidog("/home/user/duplicates", "/media/disk/origs", ignore_date=True, rename=True)
```

```
Find files by size, ignoring: date, crc32
Duplicates from the work dir at 'home' would be (if execute were True) renamed (prefixed with ✓).
Number of originals: 38
* /home/user/duplicates/foo.txt
  /media/disk/origs/foo.txt
  🔨home: renamable
  📄media: DATE WARNING + a day
Affectable: 38/38
Affected size: 59.9 kB
Warnings: 1
```

We found out all the files in the *duplicates* folder seem to be useless but one. It's date is earlier than the original one. See with full log.

```python3
Deduplidog("/home/user/duplicates", "/media/disk/origs", ignore_date=True, rename=True, set_both_to_older_date=True, logging_level=logging.INFO)
```

```
Find files by size, ignoring: date, crc32
Duplicates from the work dir at 'home' would be (if execute were True) renamed (prefixed with ✓).
Original file mtime date might be set backwards to the duplicate file.
Number of originals: 38
* /home/user/duplicates/foo.txt
  /media/disk/origs/foo.txt
  🔨home: renamable
  📄media: redatable 2022-04-28 16:58:56 -> 2020-04-26 16:58:00
* /home/user/duplicates/bar.txt
  /media/disk/origs/bar.txt
  🔨home: renamable
* /home/user/duplicates/third.txt
  /media/disk/origs/third.txt
  🔨home: renamable
  ...
Affectable: 38/38
Affected size: 59.9 kB
```

You see, the log is at the most brief, yet transparent form. The files to be affected at the work folder are prepended with the 🔨 icon whereas those affected at the original folder uses 📄 icon. We might add `execute=True` parameter to perform the actions. Or use `bashify=True` to inspect.

```python3
Deduplidog("/home/user/duplicates", "/media/disk/origs", ignore_date=True, rename=True, set_both_to_older_date=True, bashify=True)
```

The `bashify=True` just produces the commands we might use.

```bash
touch -t 1524754680.0 /media/disk/origs/foo.txt
mv -n /home/user/duplicates/foo.txt /home/user/duplicates/✓foo.txt
mv -n /home/user/duplicates/bar.txt /home/user/duplicates/✓bar.txt
mv -n /home/user/duplicates/third.txt /home/user/duplicates/✓third.txt
```

# Documentation – `Deduplidog` class

Import the `Deduplidog` class and change its parameters.

```python3
from deduplidog import Deduplidog
```

Find the duplicates. Normally, the file must have the same size, date and name. (Name might be just similar if parameters like strip_end_counter are set.) If media_magic=True, media files receive different rules: Neither the size nor the date are compared. See its help.

| parameter | type | default | description |
|-----------|------|---------|-------------|
| work_dir | str \| Path | - | Folder of the files suspectible to be duplicates. |
| original_dir | str \| Path | - | Folder of the original files. Normally, these files will not be affected.<br> (However, they might get affected by treat_bigger_as_original or set_both_to_older_date). |
| **Actions** |
| execute | bool | False | If False, nothing happens, just a safe run is performed. |
| bashify | bool | False | Print bash commands that correspond to the actions that would have been executed if execute were True.<br>    You can check and run them yourself. |
| affect_only_if_smaller | bool | False | If media_magic=True, all writing actions like rename, replace_with_original, set_both_to_older_date and treat_bigger_as_original<br>    are executed only if the affectable file is smaller than the other. |
| rename | bool | False | If execute=True, prepend ✓ to the duplicated work file name (or possibly to the original file name if treat_bigger_as_original).<br>    Mutually exclusive with replace_with_original and delete. |
| delete | bool | False | If execute=True, delete theduplicated work file name (or possibly to the original file name if treat_bigger_as_original).<br>Mutually exclusive with replace_with_original and rename. |
| replace_with_original | bool | False | If execute=True, replace duplicated work file with the original (or possibly vice versa if treat_bigger_as_original).<br>Mutually exclusive with rename and delete. |
| set_both_to_older_date | bool | False | If execute=True, media_magic=True or (media_magic=False and ignore_date=True), both files are set to the older date. Ex: work file get's the original file's date or vice versa. |
| treat_bigger_as_original | bool | False | If execute=True and rename=True and media_magic=True, the original file might be affected (by renaming) if smaller than the work file. |
| **Matching** |
| casefold | bool | False | Case insensitive file name comparing. |
| checksum | bool | False | If media_magic=False and ignore_size=False, files will be compared by CRC32 checksum. <br> (This mode is considerably slower.) |
| tolerate_hour | int \| tuple[int, int] \| bool | False | When comparing files in work_dir and media_magic=False, tolerate hour difference.<br>    Sometimes when dealing with FS changes, files might got shifted few hours.<br>    * bool → -1 .. +1<br>    * int → -int .. +int<br>    * tuple → int1 .. int2<br>    Ex: tolerate_hour=2 → work_file.st_mtime -7200 ... + 7200 is compared to the original_file.st_mtime  |
| ignore_date | bool | False | If media_magic=False, files will not be compared by date. |
| ignore_size | bool | False | If media_magic=False, files will not be compared by size. |
| space2char | bool \| str | False | When comparing files in work_dir, consider space as another char. Ex: "file 012.jpg" is compared as "file_012.jpg"  |
| strip_end_counter | bool | False | When comparing files in work_dir, strip the counter. Ex: "00034(3).MTS" is compared as "00034.MTS"  |
| strip_suffix | str | False | When comparing files in work_dir, strip the file name end matched by a regular. Ex: "001-edited.jpg" is compared as "001.jpg"  |
| work_file_stem_shortened | int | None | Photos downloaded from Google have its stem shortened to 47 chars. For the comparing purpose, treat original folder file names shortened. |
| **Media** |
| media_magic | bool | False | Nor the size or date is compared for files with media suffixes.<br>A video is considered a duplicate if it has the same name and a similar number of frames, even if it has a different extension.<br>An image is considered a duplicate if it has the same name and a similar image hash, even if the files are of different sizes.<br>(This mode is considerably slower.) |
| accepted_frame_delta | int | 1 | Used only when media_magic is True |
| accepted_img_hash_diff | int | 1 | Used only when media_magic is True |
| img_compare_date | bool | False | If True and media_magic=True, the file date or the EXIF date must match. |