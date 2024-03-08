[![Build Status](https://github.com/CZ-NIC/deduplidog/actions/workflows/run-unittest.yml/badge.svg)](https://github.com/CZ-NIC/deduplidog/actions)

Yet another file deduplicator.

## What are the use cases?
* I have downloaded photos and videos from the cloud. Oh, both Google Photos and Youtube shrink the file and changes the format. Moreover, it have shortened the file name to 47 characters and capitalize the extension. So how should I know that I have them all backed up offline?
* My disk is cluttered with several backups and I'd like to be sure these are all just copies.

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