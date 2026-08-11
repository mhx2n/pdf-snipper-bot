"""PDF page extraction + smart compression for scanned (image-based) PDFs."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Iterable, List, Tuple

import pikepdf
from PIL import Image
from pypdf import PdfReader, PdfWriter


@dataclass(frozen=True)
class QualityMode:
    key: str
    label: str
    max_side: int | None  # None = keep original pixels
    jpeg_quality: int | None  # None = no image re-encode (lossless only)


MODES = {
    "original": QualityMode("original", "🅾️ অরিজিনাল (১০০% কোয়ালিটি)", None, None),
    "smart": QualityMode("smart", "⭐ স্মার্ট (রেকমেন্ডেড)", 1000, 65),
    "max": QualityMode("max", "🗜️ ম্যাক্স কম্প্রেস (সবচেয়ে ছোট)", 900, 50),
}
DEFAULT_MODE = "smart"


class PdfError(Exception):
    pass


def parse_ranges(text: str, total_pages: int, max_pages: int) -> List[int]:
    """'12-40, 55, 90-97' -> sorted unique 0-based page indices."""
    text = (text or "").replace("।", ",").replace(";", ",")
    # Bengali digits -> ASCII
    text = text.translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
    pages: set[int] = set()
    for chunk in text.split(","):
        chunk = chunk.strip().replace("–", "-").replace("—", "-")
        if not chunk:
            continue
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            try:
                start, end = int(a.strip()), int(b.strip())
            except ValueError:
                raise PdfError(f"ভুল ফরম্যাট: `{chunk}`")
            if start > end:
                start, end = end, start
        else:
            try:
                start = end = int(chunk)
            except ValueError:
                raise PdfError(f"ভুল ফরম্যাট: `{chunk}`")
        if start < 1 or end > total_pages:
            raise PdfError(f"`{chunk}` — পেজ নম্বর ১ থেকে {total_pages} এর মধ্যে হতে হবে।")
        pages.update(range(start - 1, end))
    if not pages:
        raise PdfError("কোনো পেজ পাওয়া যায়নি। উদাহরণ: `12-40, 55, 90-97`")
    if len(pages) > max_pages:
        raise PdfError(f"একসাথে সর্বোচ্চ {max_pages} পৃষ্ঠা — আপনি চেয়েছেন {len(pages)} টি।")
    return sorted(pages)


def page_count(path: str) -> int:
    return len(PdfReader(path).pages)


def extract_pages(src_path: str, pages: Iterable[int], out_path: str) -> str:
    reader = PdfReader(src_path)
    writer = PdfWriter()
    for idx in pages:
        writer.add_page(reader.pages[idx])
    writer.compress_identical_objects()
    with open(out_path, "wb") as fh:
        writer.write(fh)
    return out_path


def _recompress_images(pdf: pikepdf.Pdf, mode: QualityMode) -> None:
    if mode.jpeg_quality is None:
        return
    seen: set[int] = set()
    for page in pdf.pages:
        resources = page.get("/Resources")
        xobjects = resources.get("/XObject") if resources is not None else None
        if xobjects is None:
            continue
        for name in list(xobjects.keys()):
            obj = xobjects[name]
            if obj.get("/Subtype") != "/Image":
                continue
            key = id(obj.objgen) if obj.objgen == (0, 0) else hash(obj.objgen)
            if key in seen:
                continue
            seen.add(key)
            try:
                pil = pikepdf.PdfImage(obj).as_pil_image()
            except Exception:
                continue
            if pil.mode not in ("RGB", "L"):
                pil = pil.convert("L" if pil.mode in ("1", "LA") else "RGB")
            if mode.max_side:
                longest = max(pil.size)
                if longest > mode.max_side:
                    scale = mode.max_side / longest
                    pil = pil.resize(
                        (max(1, int(pil.width * scale)), max(1, int(pil.height * scale))),
                        Image.LANCZOS,
                    )
            buf = io.BytesIO()
            pil.save(buf, format="JPEG", quality=mode.jpeg_quality, optimize=True, progressive=True)
            data = buf.getvalue()
            new_img = pikepdf.Stream(pdf, data)
            new_img.Type = pikepdf.Name("/XObject")
            new_img.Subtype = pikepdf.Name("/Image")
            new_img.Width, new_img.Height = pil.width, pil.height
            new_img.BitsPerComponent = 8
            new_img.ColorSpace = pikepdf.Name(
                "/DeviceGray" if pil.mode == "L" else "/DeviceRGB"
            )
            new_img.Filter = pikepdf.Name("/DCTDecode")
            xobjects[name] = new_img


def compress(src_path: str, out_path: str, mode_key: str) -> str:
    mode = MODES.get(mode_key, MODES[DEFAULT_MODE])
    with pikepdf.open(src_path, allow_overwriting_input=True) as pdf:
        _recompress_images(pdf, mode)
        pdf.remove_unreferenced_resources()
        pdf.save(
            out_path,
            compress_streams=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            linearize=False,
        )
    return out_path


def build(src_path: str, pages: List[int], mode_key: str, workdir: str, stem: str) -> str:
    cut = os.path.join(workdir, f"{stem}_cut.pdf")
    extract_pages(src_path, pages, cut)
    if MODES.get(mode_key, MODES[DEFAULT_MODE]).jpeg_quality is None:
        return cut
    out = os.path.join(workdir, f"{stem}_out.pdf")
    try:
        compress(cut, out, mode_key)
    except Exception:
        return cut
    if os.path.getsize(out) >= os.path.getsize(cut):
        return cut
    os.remove(cut)
    return out


def split_by_size(path: str, pages_count: int, limit_bytes: int, workdir: str) -> List[str]:
    """Split an output PDF into chunks that each fit under limit_bytes."""
    size = os.path.getsize(path)
    if size <= limit_bytes or pages_count <= 1:
        return [path]
    parts_needed = int(size // limit_bytes) + 1
    per_part = max(1, pages_count // parts_needed + (1 if pages_count % parts_needed else 0))
    reader = PdfReader(path)
    outputs: List[str] = []
    for i in range(0, pages_count, per_part):
        writer = PdfWriter()
        for p in range(i, min(i + per_part, pages_count)):
            writer.add_page(reader.pages[p])
        part_path = os.path.join(workdir, f"part_{len(outputs) + 1}.pdf")
        with open(part_path, "wb") as fh:
            writer.write(fh)
        outputs.append(part_path)
    return outputs


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


def summarize(pages: List[int]) -> Tuple[str, int]:
    """Compact '31-70, 90' style label + count."""
    if not pages:
        return "", 0
    parts, start, prev = [], pages[0], pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        parts.append(f"{start + 1}" if start == prev else f"{start + 1}-{prev + 1}")
        start = prev = p
    parts.append(f"{start + 1}" if start == prev else f"{start + 1}-{prev + 1}")
    return ", ".join(parts), len(pages)