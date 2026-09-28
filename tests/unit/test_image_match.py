"""Template matching and visual regression, tested against synthetic images (no device needed)."""

import pytest
from PIL import Image, ImageDraw

from engine.image_match import compare_images, find_template, parse_region


def make_screen(size=(400, 300), bg=(20, 20, 20)) -> Image.Image:
    return Image.new("RGB", size, bg)


def paste_icon(screen: Image.Image, box, primary=(200, 50, 50), secondary=(250, 220, 60)) -> Image.Image:
    """A two-color icon (a dot inside a square), not a flat block.

    A flat, single-color template has ~zero variance, which makes normalized cross-correlation
    degenerate (it reports a "perfect match" everywhere, including on a blank background) - a
    real icon always has internal detail, so the fixture should too.
    """
    screen = screen.copy()
    draw = ImageDraw.Draw(screen)
    x1, y1, x2, y2 = box
    draw.rectangle(box, fill=primary)
    draw.ellipse((x1 + 8, y1 + 8, x2 - 8, y2 - 8), fill=secondary)
    return screen


@pytest.fixture
def paths(tmp_path):
    screen = make_screen()
    screen = paste_icon(screen, (150, 80, 190, 120))  # a 40x40 "icon" at (150,80)
    screen_path = tmp_path / "screen.png"
    screen.save(screen_path)

    template = screen.crop((150, 80, 190, 120))
    template_path = tmp_path / "template.png"
    template.save(template_path)

    other_path = tmp_path / "other.png"
    make_screen(bg=(200, 200, 200)).save(other_path)  # no icon anywhere
    return {"screen": screen_path, "template": template_path, "other": other_path, "tmp": tmp_path}


# ---- template matching -----------------------------------------------------

def test_finds_template_at_correct_location(paths):
    match = find_template(paths["screen"], paths["template"], threshold=0.9)
    assert match is not None
    assert (match.x, match.y, match.width, match.height) == (150, 80, 40, 40)
    assert match.center == (170, 100)
    assert match.score > 0.99


def test_returns_none_when_not_present(paths):
    assert find_template(paths["other"], paths["template"], threshold=0.9) is None


def test_missing_files_raise_clear_errors(paths, tmp_path):
    with pytest.raises(FileNotFoundError, match="screenshot"):
        find_template(tmp_path / "nope.png", paths["template"])
    with pytest.raises(FileNotFoundError, match="template"):
        find_template(paths["screen"], tmp_path / "nope.png")


def test_template_larger_than_screenshot_raises(paths):
    big = paths["tmp"] / "big.png"
    make_screen(size=(800, 600)).save(big)
    with pytest.raises(ValueError, match="larger than"):
        find_template(paths["template"], big)  # args swapped on purpose: template bigger than "screen"


def test_flat_template_is_rejected_instead_of_matching_spuriously(paths):
    # Regression: a single-color template makes TM_CCOEFF_NORMED degenerate, reporting a
    # spuriously perfect score almost anywhere (including on a screen with no icon at all) -
    # this must raise instead of silently returning a false-positive match.
    flat = paths["tmp"] / "flat.png"
    Image.new("RGB", (40, 40), (200, 50, 50)).save(flat)
    with pytest.raises(ValueError, match="flat color"):
        find_template(paths["other"], flat)


# ---- visual regression ------------------------------------------------------

def test_identical_images_are_100_percent_similar(paths):
    diff = compare_images(paths["screen"], paths["screen"])
    assert diff.similarity_percent == 100.0


def test_different_images_are_much_less_similar(paths):
    diff = compare_images(paths["other"], paths["screen"])
    assert diff.similarity_percent < 90.0


def test_small_change_stays_within_tolerance(paths, tmp_path):
    # A single pixel nudged by a small amount should be absorbed by the default tolerance.
    from PIL import ImageChops
    almost_same = paths["screen"].with_name("almost.png")
    img = Image.open(paths["screen"]).convert("RGB")
    px = img.load()
    px[0, 0] = tuple(min(255, c + 5) for c in px[0, 0])
    img.save(almost_same)
    diff = compare_images(almost_same, paths["screen"])
    assert diff.similarity_percent == 100.0


def test_region_restricts_comparison(paths):
    # Differences outside the region must not affect the result.
    changed = paste_icon(Image.open(paths["screen"]), (0, 0, 20, 20), primary=(0, 255, 0), secondary=(0, 100, 0))
    changed_path = paths["tmp"] / "changed.png"
    changed.save(changed_path)
    whole = compare_images(changed_path, paths["screen"])
    cropped = compare_images(changed_path, paths["screen"], region=(150, 80, 190, 120))
    assert whole.similarity_percent < 100.0
    assert cropped.similarity_percent == 100.0


def test_size_mismatch_raises(paths, tmp_path):
    small = tmp_path / "small.png"
    make_screen(size=(100, 100)).save(small)
    with pytest.raises(ValueError, match="Size mismatch"):
        compare_images(small, paths["screen"])


def test_parse_region():
    assert parse_region("0,0,100,50") == (0, 0, 100, 50)
    assert parse_region((1, 2, 3, 4)) == (1, 2, 3, 4)
    assert parse_region(None) is None
    with pytest.raises(ValueError):
        parse_region("0,0,100")
