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


def test_parse_pull_tolerates_trailing_carriage_return():
    assert protocol.parse_pull("PULL 5\r") == 5


def test_parse_pull_clamps_to_max():
    assert protocol.parse_pull("PULL 999999999") == protocol.MAX_PULL_LIMIT


def test_parse_ack_rejects_empty_ids_between_commas():
    assert protocol.parse_ack("ACK a,,b") is None


def test_format_ack_then_parse_round_trips_many_ids():
    ids = [f"{i:032x}" for i in range(50)]
    assert protocol.parse_ack(protocol.format_ack(ids)) == ids


# --- Protocol v2 (contract C1.2/C1.3) ------------------------------------------------------------

from cv_argus.sender import (  # noqa: E402
    DONE_LINE,
    MAX_DLQ_LIMIT,
    format_end,
    format_leased,
    format_result,
    format_taken,
    parse_end,
    parse_result,
    parse_taken,
)


def test_end_round_trips_both_counts():
    assert format_end(2, 5) == "END 2 5"
    assert parse_end(format_end(2, 5)) == (2, 5)
    assert parse_end("END 0 0\r") == (0, 0)


@pytest.mark.parametrize("line", ["END", "END 1", "END 1 2 3", "END -1 2", "end 1 2", " END 1 2"])
def test_parse_end_rejects_anything_but_two_counts(line):
    assert parse_end(line) is None


def test_taken_uses_the_ack_id_shape():
    ids = [f"{i:032x}" for i in range(8)]
    assert parse_taken(format_taken(ids)) == ids
    assert parse_taken("TAKEN a,b\r") == ["a", "b"]
    for bad in ("TAKEN", "TAKEN ", "TAKEN a,,b", "taken a", "TAKEN a b"):
        assert parse_taken(bad) is None, bad
    assert format_leased(0) == "LEASED 0"


def test_ok_result_round_trips():
    line = format_result(1, 2, 3, 4, 5)
    assert line == "OK acked=1 failed=2 leased=3 dlq=4 outbox=5"
    assert parse_result(line) == {"acked": 1, "failed": 2, "leased": 3, "dlq": 4, "outbox": 5}
    assert parse_result("OK acked=1 failed=2 dlq=4") is None   # the v1 shape


def test_dlq_hands_out_one_row_per_session():
    assert MAX_DLQ_LIMIT == 1
    assert protocol.parse_dlq("DLQ 10") == 1
    assert DONE_LINE == "DONE"


def test_fail_reasons_the_esp32_sends_parse():
    for reason in ("retry_window", "preempted", "route_finished", "no_route", "too_old",
                   "wrong_truck", "invalid", "no_network", "no_credentials", "timeout",
                   "transport_error", "http_503"):
        assert protocol.parse_fail(f"FAIL abc {reason} -") == ("abc", reason, None)
