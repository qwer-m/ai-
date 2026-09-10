from pathlib import Path

import pytest
from PIL import Image

from modules.testing.ui_automation import scale_normalized_coordinates


def test_normalized_coordinates_are_scaled_to_original_screenshot(tmp_path: Path) -> None:
    screenshot = tmp_path / "tablet.png"
    Image.new("RGB", (2176, 1600), color=(0, 0, 0)).save(screenshot)

    assert scale_normalized_coordinates(str(screenshot), [500, 500]) == (1088, 800)
    assert scale_normalized_coordinates(str(screenshot), [0, 0]) == (0, 0)
    assert scale_normalized_coordinates(str(screenshot), [1000, 1000]) == (2175, 1599)


def test_normalized_coordinates_reject_unknown_coordinate_space(tmp_path: Path) -> None:
    screenshot = tmp_path / "tablet.png"
    Image.new("RGB", (2176, 1600), color=(0, 0, 0)).save(screenshot)

    with pytest.raises(ValueError, match="between 0 and 1000"):
        scale_normalized_coordinates(str(screenshot), [1024, 500])
