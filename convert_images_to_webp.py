#!/usr/bin/env python3
"""Back up raster images, create optimized WebP copies, and update local references."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageSequence


SOURCE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
SOURCE_DIRECTORIES_TO_SKIP = {".git", "_image_backups"}
REFERENCE_EXTENSIONS = {".html", ".css", ".js", ".mjs", ".cjs"}
MAX_WIDTH = 1200
QUALITY = 75
METHOD = 6


def source_images(root: Path) -> list[Path]:
    found: list[Path] = []
    for current, directories, filenames in os.walk(root):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory not in SOURCE_DIRECTORIES_TO_SKIP
            and not (Path(current) / directory).is_symlink()
        )
        found.extend(
            Path(current) / filename
            for filename in filenames
            if Path(filename).suffix.lower() in SOURCE_EXTENSIONS
            and not (Path(current) / filename).is_symlink()
        )
    return sorted(found)


def image_frames(image: Image.Image) -> list[Image.Image]:
    frames: list[Image.Image] = []
    for source_frame in ImageSequence.Iterator(image):
        frame = source_frame.copy()
        if frame.width > MAX_WIDTH:
            height = round(frame.height * MAX_WIDTH / frame.width)
            frame = frame.resize((MAX_WIDTH, height), Image.Resampling.LANCZOS)
        if frame.mode not in {"RGB", "RGBA"}:
            frame = frame.convert("RGBA" if "A" in frame.getbands() or "transparency" in frame.info else "RGB")
        frames.append(frame)
    return frames


def convert(source: Path, destination: Path) -> None:
    with Image.open(source) as image:
        frames = image_frames(image)
        options: dict[str, object] = {"quality": QUALITY, "method": METHOD}
        if image.info.get("icc_profile"):
            options["icc_profile"] = image.info["icc_profile"]
        if image.info.get("exif"):
            options["exif"] = image.info["exif"]
        if len(frames) > 1:
            options["save_all"] = True
            options["append_images"] = frames[1:]
            for key in ("duration", "loop", "background"):
                if key in image.info:
                    options[key] = image.info[key]

        with tempfile.NamedTemporaryFile(
            dir=destination.parent, suffix=".webp", delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        try:
            frames[0].save(temporary_path, format="WEBP", **options)
            with Image.open(temporary_path) as check:
                check.verify()
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)


def update_references(root: Path, conversions: dict[Path, Path]) -> int:
    updated_files = 0
    for current, directories, filenames in os.walk(root):
        directories[:] = [
            directory
            for directory in directories
            if directory not in SOURCE_DIRECTORIES_TO_SKIP
        ]
        for filename in filenames:
            path = Path(current) / filename
            if path.suffix.lower() not in REFERENCE_EXTENSIONS or path.is_symlink():
                continue

            original_bytes = path.read_bytes()
            has_bom = original_bytes.startswith(b"\xef\xbb\xbf")
            text = original_bytes.decode("utf-8-sig")
            updated_text = text
            for source, destination in conversions.items():
                relative_path = Path(os.path.relpath(source, path.parent)).as_posix()
                replacement_path = Path(os.path.relpath(destination, path.parent)).as_posix()
                pattern = re.compile(re.escape(relative_path), re.IGNORECASE)
                updated_text = pattern.sub(lambda _: replacement_path, updated_text)

            if updated_text != text:
                encoded = updated_text.encode("utf-8")
                path.write_bytes((b"\xef\xbb\xbf" if has_bom else b"") + encoded)
                updated_files += 1
    return updated_files


def main() -> int:
    root = Path(__file__).resolve().parent
    backup_root = root / "_image_backups"
    images = source_images(root)
    if not images:
        print("No PNG or JPEG images found.")
        return 0

    destinations = [source.with_suffix(".webp") for source in images]
    if len(set(destinations)) != len(destinations):
        print("Multiple source files map to the same WebP path; no conversions were started.", file=sys.stderr)
        return 1

    try:
        for source, destination in zip(images, destinations):
            if destination.exists() and destination.resolve() != source.resolve():
                with Image.open(destination) as existing:
                    if existing.format != "WEBP":
                        raise ValueError(f"existing destination is not WebP: {destination}")
                    existing.verify()

        for source in images:
            backup = backup_root / source.relative_to(root)
            if not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, backup)
            destination = source.with_suffix(".webp")
            if destination.exists():
                destination_backup = backup_root / destination.relative_to(root)
                if not destination_backup.exists():
                    destination_backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, destination_backup)
    except OSError as error:
        print(f"Could not back up images; no conversions were started: {error}", file=sys.stderr)
        return 1

    conversions: dict[Path, Path] = {}
    failures = 0
    for source in images:
        destination = source.with_suffix(".webp")
        try:
            convert(source, destination)
            conversions[source] = destination
            print(f"{source.relative_to(root)} -> {destination.relative_to(root)}")
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
            failures += 1
            print(f"ERROR: {source.relative_to(root)}: {error}", file=sys.stderr)

    if conversions:
        changed_files = update_references(root, conversions)
        print(f"Updated references in {changed_files} source file(s).")

    print(f"Converted {len(conversions)}/{len(images)} images.")
    if failures:
        print(f"{failures} image(s) failed; their source files remain unchanged.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
