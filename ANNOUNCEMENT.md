# StorageMark: find what fills your disk, from the terminal

StorageMark is a free disk-space analyser for macOS and Linux that runs in a
terminal. It looks like a vintage Norton Commander: blue panels, cyan frames
and yellow highlights in the old CGA colours, with every action on the
keyboard.

A small C program scans the folder you choose. The results appear in five
views:

- **folder tree:** where the space is;
- **file list:** every file, sortable by size or date;
- **file types:** space by extension;
- **age:** files grouped by how long ago they were last changed;
- **what-if:** what removing the marked items would free.

**Rules** point at likely junk: caches, build output, old installers, big
media and logs. On Linux there are also rules for the Desktop Trash and the
systemd journal. Press `F` on any file or folder to turn it into a rule of
your own.

**Removal is careful.** Mark items, review the exact list, then move them to
the Trash, which is your undo. Permanent delete requires typing `yes`.
Protected and recently changed folders get a warning.

It works over SSH, so you can use it on servers. On Linux it skips `/proc`,
`/sys` and other virtual filesystems but keeps real disks. On a small Ubuntu
server, scanning `/` took about 5 seconds.

Install, once you have a C compiler, git and [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/markus-wolf/FileManager
storagemark ~
```

Source and documentation: https://github.com/markus-wolf/FileManager

---

## Short version

> StorageMark: a terminal disk-space analyser for macOS and Linux, styled like
> a vintage Norton Commander. A fast C scanner, five views (tree, files, types,
> age, what-if), rules that find caches, build output and old installers, and
> Trash-first removal. Works over SSH.
> `uv tool install git+https://github.com/markus-wolf/FileManager`
> https://github.com/markus-wolf/FileManager
