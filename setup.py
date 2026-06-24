"""Build hook: compile the standalone C scanner during the wheel build.

The C scanner is a standalone executable (not a Python C-extension), so we
compile it with `cc` in a custom build_py step and ship the resulting binary
as package data inside `storagemark/c/`. The C sources are shipped too, so
the package can recompile the scanner at runtime if the binary is missing
(see storagemark/python/scanner.py:_compile_scanner).

If no compiler is available at build time we warn and continue, rather than
failing the whole install — the runtime fallback handles it on first run.
"""
import os
import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

C_DIR   = Path(__file__).parent / "storagemark" / "c"
BINARY  = C_DIR / "storagescanner"
SOURCES = ["storagescanner.c", "hashset.c"]


def compile_scanner() -> None:
    cc = os.environ.get("CC", "cc")
    cmd = [cc, "-O2", "-Wall", "-std=c11", "-o", str(BINARY)] + \
          [str(C_DIR / s) for s in SOURCES]
    print("storagemark: compiling scanner ->", " ".join(cmd))
    subprocess.run(cmd, check=True)


class BuildPyWithScanner(build_py):
    def run(self):
        try:
            compile_scanner()
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
            sys.stderr.write(
                "\nstoragemark WARNING: could not compile the C scanner "
                f"({e}).\nInstall Xcode Command Line Tools "
                "(xcode-select --install).\nStorageMark will try to compile "
                "it automatically on first run.\n\n"
            )
        super().run()


setup(cmdclass={"build_py": BuildPyWithScanner})
