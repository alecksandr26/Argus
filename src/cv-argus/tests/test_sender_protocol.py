"""`sender/protocol.py` — pure PULL/ACK grammar parsing/formatting, no transport involved."""

from cv_argus.sender import (
    format_ack,
    format_acked,
    format_pull,
    parse_ack,
    parse_pull,
)


def test_parse_pull_valid():
    assert parse_pull("PULL 10") == 10


def test_parse_pull_zero_is_valid():
    assert parse_pull("PULL 0") == 0


def test_parse_pull_rejects_non_numeric():
    assert parse_pull("PULL abc") is None


def test_parse_pull_is_case_sensitive():
    assert parse_pull("pull 10") is None


def test_parse_pull_rejects_missing_count():
    assert parse_pull("PULL") is None


def test_format_pull_round_trips():
    assert parse_pull(format_pull(7)) == 7


def test_parse_ack_valid_single_id():
    assert parse_ack("ACK abc123") == ["abc123"]


def test_parse_ack_valid_multiple_ids():
    assert parse_ack("ACK abc,def,ghi") == ["abc", "def", "ghi"]


def test_parse_ack_rejects_no_ids():
    assert parse_ack("ACK") is None
    assert parse_ack("ACK ") is None


def test_parse_ack_rejects_empty_string():
    assert parse_ack("") is None


def test_parse_ack_rejects_garbage():
    assert parse_ack("GARBAGE") is None


def test_format_ack_round_trips():
    ids = ["a1", "b2", "c3"]
    assert parse_ack(format_ack(ids)) == ids


def test_format_acked():
    assert format_acked(3) == "ACKED 3"


# --- Grammar corner cases ------------------------------------------------------------------------

import pytest  # noqa: E402

from cv_argus.sender import protocol  # noqa: E402


def test_parse_pull_rejects_negative_and_spaces():
    assert protocol.parse_pull("PULL -1") is None
    assert protocol.parse_pull("PULL  5") is None
    assert protocol.parse_pull(" PULL 5") is None


@pytest.mark.xfail(strict=True, reason="SND-04: trailing \\r from a CRLF client isn't tolerated")
def test_parse_pull_tolerates_trailing_carriage_return():
    assert protocol.parse_pull("PULL 5\r") == 5


@pytest.mark.xfail(strict=True, reason="SND-05: no protocol maximum for PULL")
def test_parse_pull_clamps_to_max():
    assert protocol.parse_pull("PULL 999999999") == protocol.MAX_PULL_LIMIT


def test_parse_ack_rejects_empty_ids_between_commas():
    assert protocol.parse_ack("ACK a,,b") is None


def test_format_ack_then_parse_round_trips_many_ids():
    ids = [f"{i:032x}" for i in range(50)]
    assert protocol.parse_ack(protocol.format_ack(ids)) == ids
