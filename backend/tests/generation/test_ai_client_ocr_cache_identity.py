from core.ai.ai_client_impl import AIClient


def test_local_image_cache_identity_uses_content_instead_of_temp_path(tmp_path):
    image_bytes = b"real-image-content-for-cache-identity"
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    first_path.write_bytes(image_bytes)
    second_path.write_bytes(image_bytes)

    first_key = AIClient._build_ocr_cache_key(
        f"file://{first_path}",
        "OCR",
        "vision-model-a",
    )
    second_key = AIClient._build_ocr_cache_key(
        f"file://{second_path}",
        "OCR",
        "vision-model-a",
    )

    assert first_key == second_key
    assert str(first_path) not in first_key
    assert str(second_path) not in second_key


def test_local_image_cache_identity_changes_with_content_or_model(tmp_path):
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    first_path.write_bytes(b"image-content-a")
    second_path.write_bytes(b"image-content-b")

    baseline_key = AIClient._build_ocr_cache_key(
        str(first_path),
        "OCR",
        "vision-model-a",
    )

    assert baseline_key != AIClient._build_ocr_cache_key(
        str(second_path),
        "OCR",
        "vision-model-a",
    )
    assert baseline_key != AIClient._build_ocr_cache_key(
        str(first_path),
        "OCR",
        "vision-model-b",
    )


def test_remote_image_cache_identity_keeps_url():
    image_url = "https://example.test/assets/page.png?version=2"

    cache_key = AIClient._build_ocr_cache_key(
        image_url,
        "OCR",
        "vision-model-a",
    )

    assert image_url in cache_key


def test_ocr_json_cache_value_is_restored_as_text():
    cached_value = {
        "visible_text": ["同步作文"],
        "ui_elements": ["课程入口"],
    }

    restored = AIClient._cached_text_response(cached_value)

    assert isinstance(restored, str)
    assert "同步作文" in restored


def test_ocr_v2_cache_value_preserves_original_text_format():
    original = '{\n    "visible_text": ["同步作文"]\n}'
    cached_value = {
        "cache_contract": AIClient._OCR_CACHE_VALUE_CONTRACT,
        "text": original,
    }

    assert AIClient._cached_text_response(cached_value) == original
