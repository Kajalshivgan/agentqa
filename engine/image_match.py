"""Image verification: template matching (find a reference image anywhere on screen) and
full-screen visual regression (diff the current screenshot against a saved baseline).

Both work on saved PNG file paths, so they compose with `ADBClient.screenshot()` with no extra
plumbing. Kept separate from MobileUILibrary.py the same way ui_tree.py is: no Robot Framework
or ADB dependency, so it's unit-testable with synthetic images and no device.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
from PIL import Image, ImageChops

# Below this grayscale standard deviation, a template is "flat": normalized cross-correlation
# (TM_CCOEFF_NORMED) divides by the template's own variance, so a near-uniform template makes the
# match numerically degenerate and reports a spuriously perfect score almost anywhere, including
# on backgrounds that share nothing with it. There's no threshold or algorithm change that fixes
# this - the template itself has to have real visual detail.
MIN_TEMPLATE_STDDEV = 4.0


@dataclass
class Match:
    x: int
    y: int
    width: int
    height: int
    score: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.width, self.y + self.height


def find_template(screenshot: str | Path, template: str | Path, threshold: float = 0.8) -> Match | None:
    """Locate `template` inside `screenshot` by normalized cross-correlation (grayscale).

    Returns the best match if its score clears `threshold` (0-1), else None. Raises
    FileNotFoundError if either image can't be read, or ValueError if the template is larger
    than the screenshot (a common mistake: cropping the template from a different resolution).
    """
    screen_img = cv2.imread(str(screenshot), cv2.IMREAD_GRAYSCALE)
    if screen_img is None:
        raise FileNotFoundError(f"Could not read screenshot: {screenshot}")
    template_img = cv2.imread(str(template), cv2.IMREAD_GRAYSCALE)
    if template_img is None:
        raise FileNotFoundError(f"Could not read template image: {template} "
                                f"(add it under the library's templates_dir)")
    th, tw = template_img.shape
    sh, sw = screen_img.shape
    if th > sh or tw > sw:
        raise ValueError(f"Template {template} ({tw}x{th}) is larger than the screenshot ({sw}x{sh}) "
                         f"- was it cropped at a different screen resolution?")
    if template_img.std() < MIN_TEMPLATE_STDDEV:
        raise ValueError(f"Template {template} is nearly a single flat color (std dev "
                         f"{template_img.std():.1f} < {MIN_TEMPLATE_STDDEV}) - matching would be "
                         f"unreliable (spurious near-perfect scores almost anywhere). Crop a region "
                         f"with real visual detail, e.g. include part of an icon's outline or an "
                         f"adjacent edge, not just its interior.")

    result = cv2.matchTemplate(screen_img, template_img, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < threshold:
        return None
    x, y = max_loc
    return Match(x, y, tw, th, float(max_val))


@dataclass
class ImageDiff:
    similarity_percent: float
    diff_image: Image.Image  # per-pixel absolute difference, for saving/embedding on failure
    baseline_size: tuple[int, int]
    actual_size: tuple[int, int]


def compare_images(actual: str | Path, baseline: str | Path, region: tuple[int, int, int, int] | None = None,
                   tolerance: int = 30) -> ImageDiff:
    """Compare `actual` against `baseline`.

    `region` (left, top, right, bottom) restricts the comparison to a crop of both images -
    useful to exclude a ticking clock or other expected-to-change area. `tolerance` is the
    per-channel (0-255) difference below which a pixel still counts as "the same", absorbing
    anti-aliasing and minor rendering noise rather than a real visual difference.
    """
    actual_img = Image.open(actual).convert("RGB")
    baseline_img = Image.open(baseline).convert("RGB")
    if region is not None:
        actual_img = actual_img.crop(region)
        baseline_img = baseline_img.crop(region)
    if actual_img.size != baseline_img.size:
        raise ValueError(f"Size mismatch: baseline is {baseline_img.size}, actual is {actual_img.size} "
                         f"(different device/resolution, or the baseline needs updating)")

    diff = ImageChops.difference(actual_img, baseline_img)
    diff_gray = diff.convert("L")
    histogram = diff_gray.histogram()
    total = diff_gray.size[0] * diff_gray.size[1]
    different = sum(histogram[tolerance + 1:])
    similarity = 100.0 * (1 - different / total) if total else 100.0
    return ImageDiff(similarity, diff, baseline_img.size, actual_img.size)


def parse_region(value: str | tuple[int, int, int, int] | None) -> tuple[int, int, int, int] | None:
    """Accept a Robot Framework string arg ("x1,y1,x2,y2") or an actual tuple; None passes through."""
    if value is None or isinstance(value, tuple):
        return value
    parts = [int(p.strip()) for p in str(value).split(",")]
    if len(parts) != 4:
        raise ValueError(f"region must be 'left,top,right,bottom', got: {value!r}")
    return tuple(parts)  # type: ignore[return-value]
