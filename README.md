Yet another folder deduplicator.

## What are the use cases?
* I have downloaded photos and videos from the cloud. Oh, both Google Photos and Youtube shrinks the file and changes the format so how should I know that I have them all backed up offline?
* My disk is cluttered with several backups and I'd like to be sure these are all just copies.

## What is compared?

* The file name.

Works great when the files keep more or less the same name. (Photos downloaded from Google have its stem shortened to 47 chars but that is enough.) Might ignore case sensitivity.

* The file date.

You can impose the same file *mtime*, tolerate few hours (to correct timezone confusion) or ignore the date altogether.

* The file size or the media hash.

The file must have the same size. Or take advantage of the media magic under the hood which ignores the file size but compares the image or the video inside. It is great whenever you end up with some files converted to a different format.

* Not the contents.

Does not perform a byte-to-byte check. When the file names might differ or you need to check there is no byte corruption, use i.e. [jdupes](https://www.jdupes.com/).

## Why not using standard sync tools like [meld](https://meldmerge.org/)?
These imply the folders have the same structure. Deduplidog is tolerant towards files scattered around.