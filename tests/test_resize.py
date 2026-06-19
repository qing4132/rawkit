"""Unit tests for the shared compute_target_size helper."""

from __future__ import annotations

import pytest

from rawkit._resize import compute_target_size


def test_no_target_returns_input_unchanged() -> None:
    assert compute_target_size(4000, 3000) == (4000, 3000)


def test_long_edge_landscape() -> None:
    # 4000x3000, long=2000 → ratio 0.5
    assert compute_target_size(4000, 3000, long_edge=2000) == (2000, 1500)


def test_long_edge_portrait() -> None:
    # 3000x4000, long=2000 → ratio 0.5 on the 4000 axis
    assert compute_target_size(3000, 4000, long_edge=2000) == (1500, 2000)


def test_long_edge_no_upscale() -> None:
    # 1000x800 with long=2000 → unchanged (don't upscale)
    assert compute_target_size(1000, 800, long_edge=2000) == (1000, 800)


def test_short_edge_landscape() -> None:
    # 4000x3000, short=1080 → ratio 0.36 → 1440x1080
    assert compute_target_size(4000, 3000, short_edge=1080) == (1440, 1080)


def test_short_edge_portrait() -> None:
    # 3000x4000, short=1080 → ratio 0.36 on the 3000 axis → 1080x1440
    assert compute_target_size(3000, 4000, short_edge=1080) == (1080, 1440)


def test_short_edge_no_upscale() -> None:
    # 800x600, short=1080 → already smaller, unchanged
    assert compute_target_size(800, 600, short_edge=1080) == (800, 600)


def test_megapixels_downscales() -> None:
    # 4000x3000 = 12 MP, target 3 MP → ratio = sqrt(3/12) = 0.5 → 2000x1500
    assert compute_target_size(4000, 3000, megapixels=3.0) == (2000, 1500)


def test_megapixels_no_upscale() -> None:
    # 1000x1000 = 1 MP, target 12 MP → unchanged
    assert compute_target_size(1000, 1000, megapixels=12.0) == (1000, 1000)


def test_megapixels_preserves_aspect_ratio() -> None:
    # Verify aspect ratio is kept across megapixel scaling.
    new_w, new_h = compute_target_size(8000, 4000, megapixels=2.0)
    assert abs(new_w / new_h - 2.0) < 0.01


def test_multiple_targets_rejected() -> None:
    with pytest.raises(ValueError, match="at most one"):
        compute_target_size(1000, 800, long_edge=500, short_edge=400)
    with pytest.raises(ValueError, match="at most one"):
        compute_target_size(1000, 800, long_edge=500, megapixels=2.0)
    with pytest.raises(ValueError, match="at most one"):
        compute_target_size(1000, 800, short_edge=400, megapixels=2.0)


def test_extreme_downscale_clamps_to_at_least_1px() -> None:
    # Pathological: target so small that round() would yield 0
    assert compute_target_size(10000, 1, long_edge=1) == (1, 1)
