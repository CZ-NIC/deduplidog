<p align="center"><b>Deduplidog</b> – Deduplicator that covers your back.</p>
<p align="center">
  <img src="./asset/logo.jpg" />
</p>

[![Build Status](https://github.com/CZ-NIC/deduplidog/actions/workflows/run-unittest.yml/badge.svg)](https://github.com/CZ-NIC/deduplidog/actions)


- [About](#about)
   * [What are the use cases?](#what-are-the-use-cases)
   * [What is compared?](#what-is-compared)
   * [Why not using standard sync tools like meld?](#why-not-using-standard-sync-tools-like-meld)
   * [Doubts?](#doubts)
- [Launch](#launch)
- [Examples](#examples)
   * [Duplicated files](#duplicated-files)
   * [Names shuffled](#names-shuffled)
- [Documentation](#documentation)

# About

## What are the use cases?
* I have downloaded photos and videos from the cloud. Oh, both Google Photos and Youtube *shrink the files* and change their format. Moreover, they shorten the file names to 47 characters and capitalize the extensions. So how am I supposed to know if I have everything backed up offline when the copies are resized?
* My disk is cluttered with several backups and I'd like to be sure these are all just copies.
* I merge data from multiple sources. Some files in the backup might have *the former orignal file modification date* that I might wish to restore.

## What is compared?

* The file name.

Works great when the files keep more or less the same name. (Photos downloaded from Google have its stem shortened to 47 chars but that is enough.) Might ignore case sensitivity.

* The file date.

You can impose the same file *mtime*, tolerate few hours (to correct timezone confusion) or ignore the date altogether.

Note: we ignore smaller than a second differences.

* The file size, the image hash or the video frame count.

The file must have the same size. Or take advantage of the media magic under the hood which ignores the file size but compares the image or the video inside. It is great whenever you end up with some files converted to a different format.

* The contents?

You may use `checksum=True` to perform CRC32 check. However for byte-to-byte checking, when the file names might differ or you need to check there is no byte corruption, some other tool might be better way, i.e. [jdupes](https://www.jdupes.com/).

## Why not using standard sync tools like [meld](https://meldmerge.org/)?
These imply the folders have the same structure. Deduplidog is tolerant towards files scattered around.

## Doubts?

The program does not write anything to the disk, unless `execute=True` is set. Feel free to launch it just to inspect the recommended actions. Or set `inspect=True` to output bash commands you may launch after thorough examining.

# Launch

Install with `pip install deduplidog`.

It works as a standalone program with all the CLI, TUI and GUI interfaces. Just launch the `deduplidog` command.

# Examples

## Media magic confirmation

Let's compare two folders.

```bash
deduplidog --work-dir folder1 --original-dir folder2  --media-magic --rename --execute
```

By default, `--confirm-one-by-one` is True, causing every change to be manually confirmed before it takes effect. So even though `--execute` is there, no change happen without confirmation. The change that happen is the `--rename`, the file in the `--work-dir` will be prefixed with the `✓` character. The `--media-magic` mode considers an image a duplicate if it has the same name and a similar image hash, even if the files are of different sizes.

![Confirmation](asset/warnings_confirmation_example.avif "Confirmation, including warnings")

Note that the default button is 'No' as there are some warnings. First, the file in the folder we search for duplicates in is bigger than the one in the original folder. Second, it is also older, suggesting that it might be the actual original.


## Duplicated files
Let's take a closer look to a use-case.

```bash
deduplidog --work-dir /home/user/duplicates --original-dir /media/disk/origs" --ignore-date --rename
```

This command produced the following output:

```
Find files by size, ignoring: date, crc32
Duplicates from the work dir at 'home' would be (if execute were True) renamed (prefixed with ✓).
Number of originals: 38
* /home/user/duplicates/foo.txt
  /media/disk/origs/foo.txt
  🔨home: renamable
  📄media: DATE WARNING + a day 🛟skipped on warning
Affectable: 37/38
Affected size: 56.9 kB
Warnings: 1
```

We found out all the files in the *duplicates* folder seem to be useless but one. It's date is earlier than the original one. The life buoy icon would prevent any action. To suppress this, let's turn on `set_both_to_older_date`. See with full log.

```bash
deduplidog --work-dir /home/user/duplicates --original-dir /media/disk/origs --ignore-date --rename --set-both-to-older-date --log-level=10
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

You see, the log is at the most brief, yet transparent form. The files to be affected at the work folder are prepended with the 🔨 icon whereas those affected at the original folder uses 📄 icon. We might add `execute=True` parameter to perform the actions. Or use `inspect=True` to inspect.

```bash
deduplidog --work-dir /home/user/duplicates --original-dir /media/disk/origs --ignore-date --rename --set-both-to-older-date --inspect
```

The `inspect=True` just produces the commands we might subsequently use.

```bash
touch -t 1524754680.0 /media/disk/origs/foo.txt
mv -n /home/user/duplicates/foo.txt /home/user/duplicates/✓foo.txt
mv -n /home/user/duplicates/bar.txt /home/user/duplicates/✓bar.txt
mv -n /home/user/duplicates/third.txt /home/user/duplicates/✓third.txt
```

## Names shuffled

You face a directory that might contain some images twice. Let's analyze. We turn on `media_magic` so that we find the scaled down images. We `ignore_name` because the scaled images might have been renamed. We `skip_bigger` files as we examine the only folder and every file pair would be matched twice. That way, we declare the original image is the bigger one. And we set `log_level` verbosity so that we get a list of the affected files.

```
$ deduplidog --work-dir ~/shuffled/ --media-magic --ignore-name --skip-bigger --log-level=20
Only files with media suffixes are taken into consideration. Nor the size nor the date is compared. Nor the name!
Duplicates from the work dir at 'shuffled' (only if smaller than the pair file) would be (if execute were True) left intact (because no action is selected, nothing will happen).

Number of originals: 9
Caching image hashes: 100%|███████████████████████████████████████████████████████████████████████████████████████████████| 9/9 [00:00<00:00, 16.63it/s]
Caching working files: 9it [00:00, 62497.91it/s]
* /home/user/shuffled/IMG_20230802_shrink.jpg
  /home/user/shuffled/IMG_20230802.jpg
Affectable: 1/9
Affected size: 636.4 kB
```

We see there si a single duplicated file whose name is `IMG_20230802_shrink.jpg`.

# Documentation

See the docs overview at [https://cz-nic.github.io/deduplidog/](https://cz-nic.github.io/deduplidog/Overview/).