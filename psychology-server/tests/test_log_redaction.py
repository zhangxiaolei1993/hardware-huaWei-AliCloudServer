"""阶段 9 验收：日志脱敏（AC-27）。"""
import logging

from app.core.logging import RedactionFilter


def _render(message: str) -> str:
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None,
    )
    RedactionFilter().filter(record)
    return record.getMessage()


def test_auth_headers_are_masked():
    text = _render(
        "req headers: X-Device-Token: abc123def456, X-App-Key: k-1, "
        "X-Api-Key=api-secret, Authorization: Bearer zzz"
    )
    assert "abc123def456" not in text
    assert "k-1" not in text
    assert "api-secret" not in text
    assert "zzz" not in text
    assert "***" in text


def test_json_secrets_and_answers_are_masked():
    text = _render(
        'body {"device_token": "abcdef012345", "answer_value": 3, '
        '"answer_text": "非常同意", "transcript": "我今天..."}'
    )
    assert "abcdef012345" not in text
    assert "非常同意" not in text
    assert "我今天" not in text


def test_normal_message_unchanged():
    message = "device atlas_001 heartbeat ok"
    assert _render(message) == message
