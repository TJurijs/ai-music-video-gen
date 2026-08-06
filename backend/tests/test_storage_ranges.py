from app.main import _parse_byte_range


def test_byte_range_parser_supports_browser_range_shapes():
    assert _parse_byte_range("bytes=0-9", 100) == (0, 9)
    assert _parse_byte_range("bytes=90-", 100) == (90, 99)
    assert _parse_byte_range("bytes=-10", 100) == (90, 99)
    assert _parse_byte_range("bytes=-500", 100) == (0, 99)


def test_byte_range_parser_rejects_invalid_and_multiple_ranges():
    assert _parse_byte_range("bytes=100-110", 100) is None
    assert _parse_byte_range("bytes=10-9", 100) is None
    assert _parse_byte_range("bytes=-0", 100) is None
    assert _parse_byte_range("bytes=0-1,5-6", 100) is None
