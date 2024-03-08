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

Find the duplicates. Normally, the file must have the same size, date and name. (Name might be just similar if parameters like strip_end_counter are set.) If media_magic=True, media files receive different rules: Neither the size nor the date are compared. See its help.

