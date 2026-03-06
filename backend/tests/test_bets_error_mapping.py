"""Unit tests for bet contract error mapping."""

from app.api.bets import _map_contract_error


def test_map_contract_error_maps_account_in_use_to_duplicate_bet_message():
    err = RuntimeError("Transaction simulation failed: account already in use")
    msg = _map_contract_error(err)
    assert msg == "You already placed an on-chain bet for this match."


def test_map_contract_error_maps_known_custom_error_code():
    err = RuntimeError("custom program error: 0x1779")
    msg = _map_contract_error(err)
    assert msg == "You already placed an on-chain bet for this match."

