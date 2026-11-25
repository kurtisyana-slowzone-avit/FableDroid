#!/usr/bin/env python3
"""Compare golden-scene captures against a reference set.

Each directory holds one PNG per scene, named after the scene. For every scene
present in both, the tool reports a structural similarity score (SSIM) and the
fraction of pixels that differ by more than a tolerance, and gates both against
thresholds. Captured frames are game content: they stay on the developer's
machine, and only the JSON summary (scores and hashes) is meant to be committed.

PNG reading and writing use only zlib and struct. The reader handles 8-bit RGB
and RGBA, non-interlaced, with all five filter types, so it reads both the
stored-block PNGs from src/diagnostics/fable2_frame_capture.h and ordinary
compressed ones. See docs/BENCHMARKING.md.
"""
import argparse
from collections import namedtuple
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import zlib

TOOL_NAME = "fabledroid-bench-golden"
TOOL_VERSION = "1.0.0"
SUMMARY_NAME = "golden_summary.json"
DIFF_SUFFIX = ".diff.png"
DEFAULT_MIN_SSIM = 0.98
DEFAULT_MAX_DIFF_FRACTION = 0.01
DEFAULT_TOLERANCE = 8
WINDOW = 8
SSIM_C1 = (0.01 * 255) ** 2
SSIM_C2 = (0.03 * 255) ** 2
SSIM_METHOD = "mean SSIM of BT.601 luma over non-overlapping 8x8 windows, edge windows clipped"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
COLOR_CHANNELS = {2: 3, 6: 4}
KNOWN_CRITICAL = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INVALID = 2

Image = namedtuple("Image", "width height channels pixels")


class GoldenError(Exception):
    """An image cannot be read, written or compared."""


# --- PNG -------------------------------------------------------------------


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw, width, height, bpp):
    stride = width * bpp
    if len(raw) != height * (stride + 1):
        raise GoldenError(f"image data is {len(raw)} bytes, expected {height * (stride + 1)}")
    out = bytearray(height * stride)
    previous = bytes(stride)
    view = memoryview(raw)
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        row = bytearray(view[start + 1:start + 1 + stride])
        if kind == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif kind == 2:
            row = bytearray((a + b) & 0xFF for a, b in zip(row, previous))
        elif kind == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(stride):
                if i >= bpp:
                    row[i] = (row[i] + _paeth(row[i - bpp], previous[i], previous[i - bpp])) & 0xFF
                else:
                    row[i] = (row[i] + previous[i]) & 0xFF
        elif kind != 0:
            raise GoldenError(f"row {y} has unknown filter type {kind}")
        out[y * stride:(y + 1) * stride] = row
        previous = row
    return bytes(out)


def decode_png(data):
    """Decode an 8-bit RGB or RGBA non-interlaced PNG into an Image."""
    if not data.startswith(PNG_SIGNATURE):
        raise GoldenError("not a PNG file")
    offset, header, idat, ended = len(PNG_SIGNATURE), None, [], False
    while offset < len(data):
        if offset + 12 > len(data):
            raise GoldenError("truncated chunk header")
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        end = offset + 12 + length
        if end > len(data):
            raise GoldenError(f"truncated {kind.decode('latin-1')} chunk")
        body = data[offset + 4:offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length:end])
        if zlib.crc32(body) & 0xFFFFFFFF != crc:
            raise GoldenError(f"CRC mismatch in {kind.decode('latin-1')} chunk")
        chunk = body[4:]
        offset = end
        if header is None and kind != b"IHDR":
            raise GoldenError("first chunk is not IHDR")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise GoldenError("malformed IHDR chunk")
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            ended = True
            break
        elif kind[0] & 0x20 == 0 and kind not in KNOWN_CRITICAL:
            raise GoldenError(f"unsupported critical chunk {kind.decode('latin-1')}")
    if not ended:
        raise GoldenError("missing IEND chunk")
    if not idat:
        raise GoldenError("missing IDAT chunk")
    width, height, depth, color, compression, filtering, interlace = header
    if width == 0 or height == 0:
        raise GoldenError("image has zero width or height")
    if depth != 8 or color not in COLOR_CHANNELS:
        raise GoldenError(f"unsupported format (bit depth {depth}, color type {color}); need 8-bit RGB or RGBA")
    if compression != 0 or filtering != 0:
        raise GoldenError("unsupported compression or filter method")
    if interlace != 0:
        raise GoldenError("interlaced PNGs are not supported")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise GoldenError(f"damaged image data: {exc}") from None
    channels = COLOR_CHANNELS[color]
    return Image(width, height, channels, _unfilter(raw, width, height, channels))


def read_png(path):
    return decode_png(Path(path).read_bytes())


def _chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_png(image):
    """Encode an Image as an 8-bit RGB or RGBA PNG (filter 0, zlib level 6)."""
    color = {3: 2, 4: 6}.get(image.channels)
    if color is None:
        raise GoldenError(f"cannot encode {image.channels} channels")
    stride = image.width * image.channels
    raw = b"".join(b"\x00" + image.pixels[y * stride:(y + 1) * stride] for y in range(image.height))
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, color, 0, 0, 0)
    return PNG_SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b"")


def write_png(path, image):
    write_atomic(Path(path), encode_png(image))


# --- Metrics ---------------------------------------------------------------


def _require_same_size(a, b):
    if (a.width, a.height) != (b.width, b.height):
        raise GoldenError(f"size mismatch: {a.width}x{a.height} vs {b.width}x{b.height}")


def _rows(image):
    stride = image.width * image.channels
    pixels = memoryview(image.pixels)
    return [bytes(pixels[y * stride:(y + 1) * stride]) for y in range(image.height)]


def luma_rows(image):
    """BT.601 luma per pixel, one list per row. Alpha is ignored."""
    ch = image.channels
    return [
        [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in zip(row[0::ch], row[1::ch], row[2::ch])]
        for row in _rows(image)
    ]


def ssim(a, b):
    """Mean SSIM of luma over non-overlapping 8x8 windows (edge windows are clipped)."""
    _require_same_size(a, b)
    la, lb = luma_rows(a), luma_rows(b)
    width, height = a.width, a.height
    total, windows = 0.0, 0
    for y0 in range(0, height, WINDOW):
        band_a, band_b = la[y0:y0 + WINDOW], lb[y0:y0 + WINDOW]
        # Per-column sums over the band; each window then sums a short slice.
        col_a = list(map(sum, zip(*band_a)))
        col_b = list(map(sum, zip(*band_b)))
        col_aa = list(map(sum, zip(*([v * v for v in row] for row in band_a))))
        col_bb = list(map(sum, zip(*([v * v for v in row] for row in band_b))))
        col_ab = list(map(sum, zip(*([p * q for p, q in zip(ra, rb)] for ra, rb in zip(band_a, band_b)))))
        rows = len(band_a)
        for x0 in range(0, width, WINDOW):
            x1 = x0 + WINDOW
            n = rows * min(WINDOW, width - x0)
            mean_a = sum(col_a[x0:x1]) / n
            mean_b = sum(col_b[x0:x1]) / n
            var_a = sum(col_aa[x0:x1]) / n - mean_a * mean_a
            var_b = sum(col_bb[x0:x1]) / n - mean_b * mean_b
            cov = sum(col_ab[x0:x1]) / n - mean_a * mean_b
            total += ((2 * mean_a * mean_b + SSIM_C1) * (2 * cov + SSIM_C2)) / (
                (mean_a * mean_a + mean_b * mean_b + SSIM_C1) * (var_a + var_b + SSIM_C2)
            )
            windows += 1
    return total / windows


def diff_mask(a, b, tolerance):
    """One byte per pixel: 1 when any RGB channel differs by more than tolerance."""
    _require_same_size(a, b)
    ca, cb = a.channels, b.channels
    mask = bytearray()
    for ra, rb in zip(_rows(a), _rows(b)):
        if ca == cb == 3 and ra == rb:
            mask += bytes(a.width)
            continue
        dr = [abs(p - q) for p, q in zip(ra[0::ca], rb[0::cb])]
        dg = [abs(p - q) for p, q in zip(ra[1::ca], rb[1::cb])]
        db = [abs(p - q) for p, q in zip(ra[2::ca], rb[2::cb])]
        mask += bytes(1 if r > tolerance or g > tolerance or bl > tolerance else 0 for r, g, bl in zip(dr, dg, db))
    return bytes(mask)


def diff_fraction(a, b, tolerance):
    mask = diff_mask(a, b, tolerance)
    return sum(mask) / len(mask)


def diff_image(reference, mask):
    """Dimmed grey reference with every differing pixel painted red."""
    pixels = bytearray()
    width = reference.width
    for y, row in enumerate(luma_rows(reference)):
        flags = mask[y * width:(y + 1) * width]
        for value, flag in zip(row, flags):
            if flag:
                pixels += b"\xff\x00\x00"
            else:
                grey = int(value) // 3
                pixels += bytes((grey, grey, grey))
    return Image(reference.width, reference.height, 3, bytes(pixels))


# --- Command line ----------------------------------------------------------


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def unit_fraction(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not 0.0 <= number <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return number


def channel_tolerance(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number") from None
    if not 0 <= number <= 255:
        raise argparse.ArgumentTypeError("must be between 0 and 255")
    return number


def build_parser():
    parser = argparse.ArgumentParser(description="Compare FableDroid golden-scene captures.")
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare", help="compare candidate captures against reference captures")
    compare.add_argument("reference", type=Path, help="directory of reference <scene>.png files")
    compare.add_argument("candidate", type=Path, help="directory of candidate <scene>.png files")
    compare.add_argument(
        "--min-ssim", type=unit_fraction, default=DEFAULT_MIN_SSIM, metavar="F",
        help=f"lowest passing SSIM (default: {DEFAULT_MIN_SSIM})",
    )
    compare.add_argument(
        "--max-diff-fraction", type=unit_fraction, default=DEFAULT_MAX_DIFF_FRACTION, metavar="F",
        help=f"highest passing fraction of differing pixels (default: {DEFAULT_MAX_DIFF_FRACTION})",
    )
    compare.add_argument(
        "--tolerance", type=channel_tolerance, default=DEFAULT_TOLERANCE, metavar="N",
        help=f"a pixel differs when any RGB channel differs by more than N (default: {DEFAULT_TOLERANCE})",
    )
    compare.add_argument("--diff-dir", type=Path, help="write <scene>.diff.png here for each failing scene")
    compare.add_argument("--out", type=Path, help=f"JSON summary path (default: <candidate>/{SUMMARY_NAME})")
    return compare, parser


def list_scenes(directory):
    return {
        path.stem: path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() == ".png" and not path.name.lower().endswith(DIFF_SUFFIX)
    }


def load(path, role, entry):
    data = path.read_bytes()
    entry[f"{role}_sha256"] = hashlib.sha256(data).hexdigest()
    try:
        return decode_png(data)
    except GoldenError as exc:
        entry["status"] = "error"
        entry["reasons"].append(f"{role}: {exc}")
        return None


def compare_scene(name, ref_path, cand_path, args):
    entry = {
        "scene": name,
        "status": "pass",
        "ssim": None,
        "diff_fraction": None,
        "reference_size": None,
        "candidate_size": None,
        "reasons": [],
        "reference_sha256": None,
        "candidate_sha256": None,
        "diff_image": None,
    }
    reference = load(ref_path, "reference", entry)
    if cand_path is None:
        entry["status"] = "missing"
        entry["reasons"].append("no candidate image")
        return entry
    candidate = load(cand_path, "candidate", entry)
    if reference is None or candidate is None:
        return entry
    entry["reference_size"] = f"{reference.width}x{reference.height}"
    entry["candidate_size"] = f"{candidate.width}x{candidate.height}"
    if entry["reference_size"] != entry["candidate_size"]:
        entry["status"] = "fail"
        entry["reasons"].append(f"size mismatch: reference {entry['reference_size']}, candidate {entry['candidate_size']}")
        return entry
    score = ssim(reference, candidate)
    mask = diff_mask(reference, candidate, args.tolerance)
    fraction = sum(mask) / len(mask)
    entry["ssim"] = round(score, 8)
    entry["diff_fraction"] = round(fraction, 8)
    if score < args.min_ssim:
        entry["reasons"].append(f"SSIM {score:.6f} below {args.min_ssim}")
    if fraction > args.max_diff_fraction:
        entry["reasons"].append(f"diff fraction {fraction:.6f} above {args.max_diff_fraction}")
    if entry["reasons"]:
        entry["status"] = "fail"
        if args.diff_dir:
            entry["diff_image"] = f"{name}{DIFF_SUFFIX}"
            write_png(args.diff_dir / entry["diff_image"], diff_image(reference, mask))
    return entry


def render_table(rows):
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = ["| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(rows[0])) + " |"]
    lines.append("| " + " | ".join("-" * width for width in widths) + " |")
    lines += ["| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |" for row in rows[1:]]
    return lines


def run_compare(args, parser):
    for directory in (args.reference, args.candidate):
        if not directory.is_dir():
            parser.error(f"{directory} is not a directory")
    if args.diff_dir and args.diff_dir.resolve() in (args.reference.resolve(), args.candidate.resolve()):
        parser.error("--diff-dir must not be the reference or candidate directory")
    references, candidates = list_scenes(args.reference), list_scenes(args.candidate)
    if not references:
        parser.error(f"{args.reference} has no .png scenes")

    scenes = []
    for name, ref_path in references.items():
        try:
            scenes.append(compare_scene(name, ref_path, candidates.get(name), args))
        except OSError as exc:
            print(f"error: {name}: {exc.strerror or exc}", file=sys.stderr)
            return EXIT_INVALID
    extras = sorted(set(candidates) - set(references))
    failed = [entry["scene"] for entry in scenes if entry["status"] != "pass"]
    summary = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "reference_dir": args.reference.resolve().name,
        "candidate_dir": args.candidate.resolve().name,
        "ssim_method": SSIM_METHOD,
        "thresholds": {
            "min_ssim": args.min_ssim,
            "max_diff_fraction": args.max_diff_fraction,
            "tolerance": args.tolerance,
        },
        "scenes": scenes,
        "extra_candidates": extras,
        "result": "fail" if failed else "pass",
    }
    out = args.out or args.candidate / SUMMARY_NAME
    try:
        write_atomic(out, (json.dumps(summary, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        print(f"error: {out.name}: cannot write summary: {exc.strerror or exc}", file=sys.stderr)
        return EXIT_INVALID

    rows = [("Scene", "SSIM", "Diff fraction", "Status", "Reason")]
    for entry in scenes:
        rows.append((
            entry["scene"],
            "-" if entry["ssim"] is None else f"{entry['ssim']:.6f}",
            "-" if entry["diff_fraction"] is None else f"{entry['diff_fraction']:.6f}",
            entry["status"] if entry["status"] == "pass" else entry["status"].upper(),
            "; ".join(entry["reasons"]),
        ))
    output = [
        f"Thresholds: SSIM >= {args.min_ssim}, diff fraction <= {args.max_diff_fraction}, "
        f"tolerance {args.tolerance}",
        "",
        *render_table(rows),
        "",
    ]
    if extras:
        output.append(f"Candidate scenes with no reference (not gated): {', '.join(extras)}")
    output.append(f"Summary: {out}")
    output.append(f"RESULT: fail ({', '.join(failed)})" if failed else "RESULT: pass")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    sys.stdout.write("\n".join(output) + "\n")
    return EXIT_FAIL if failed else EXIT_PASS


def main(argv=None):
    compare_parser, parser = build_parser()
    args = parser.parse_args(argv)
    return run_compare(args, compare_parser)


if __name__ == "__main__":
    sys.exit(main())
