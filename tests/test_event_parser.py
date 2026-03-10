"""Tests for EventParser: ABI decode, dedup, exchange type detection."""

from __future__ import annotations

import pytest
from eth_abi import encode

from config import CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE, ORDER_FILLED_EVENT_SIG
from src.models.events import ExchangeType
from src.monitor.event_parser import EventParser


def _make_log(
    *,
    address: str = CTF_EXCHANGE,
    maker: str = "0xABCDEF1234567890abcdef1234567890ABCDEF12",
    taker: str = "0x1111111111111111111111111111111111111111",
    maker_asset_id: int = 0,
    taker_asset_id: int = 99999,
    maker_amount_filled: int = 500_000_000,
    taker_amount_filled: int = 1_000_000_000,
    fee: int = 5_000_000,
    tx_hash: str = "0x" + "aa" * 32,
    log_index: int = 0,
    block_number: int = 50000000,
) -> dict:
    """Build a fake raw log matching OrderFilled ABI (indexed topics + data)."""
    order_hash = b"\x01" * 32

    # Indexed params go in topics (padded to 32 bytes)
    maker_clean = maker[2:] if maker.startswith("0x") else maker
    taker_clean = taker[2:] if taker.startswith("0x") else taker
    topic_maker = bytes.fromhex(maker_clean.lower().zfill(64))
    topic_taker = bytes.fromhex(taker_clean.lower().zfill(64))

    # Non-indexed params go in data
    data = encode(
        ["uint256", "uint256", "uint256", "uint256", "uint256"],
        [maker_asset_id, taker_asset_id,
         maker_amount_filled, taker_amount_filled, fee],
    )

    return {
        "address": address,
        "topics": [
            bytes.fromhex(ORDER_FILLED_EVENT_SIG[2:]),
            order_hash,
            topic_maker,
            topic_taker,
        ],
        "data": data,
        "transactionHash": bytes.fromhex(tx_hash[2:]),
        "logIndex": log_index,
        "blockNumber": block_number,
    }


class TestEventParser:
    def setup_method(self):
        self.parser = EventParser()
        self.timestamps = {50000000: 1700000000}

    def test_decode_buy_event(self):
        log = _make_log(maker_asset_id=0, taker_asset_id=12345)
        events = self.parser.parse_logs([log], self.timestamps)
        assert len(events) == 1
        e = events[0]
        assert e.maker_asset_id == 0
        assert e.taker_asset_id == 12345
        assert e.is_maker_buying is True
        assert e.token_id == 12345
        assert e.block_timestamp == 1700000000

    def test_decode_sell_event(self):
        log = _make_log(maker_asset_id=12345, taker_asset_id=0)
        events = self.parser.parse_logs([log], self.timestamps)
        assert len(events) == 1
        e = events[0]
        assert e.is_maker_buying is False
        assert e.token_id == 12345

    def test_usdc_amount_6_decimals(self):
        log = _make_log(maker_asset_id=0, maker_amount_filled=5_000_000)
        events = self.parser.parse_logs([log], self.timestamps)
        assert events[0].usdc_amount == 5.0

    def test_exchange_type_ctf(self):
        log = _make_log(address=CTF_EXCHANGE)
        events = self.parser.parse_logs([log], self.timestamps)
        assert events[0].exchange_type == ExchangeType.CTF

    def test_exchange_type_neg_risk(self):
        log = _make_log(address=NEG_RISK_CTF_EXCHANGE)
        events = self.parser.parse_logs([log], self.timestamps)
        assert events[0].exchange_type == ExchangeType.NEG_RISK

    def test_dedup_same_tx_log(self):
        log = _make_log(tx_hash="0x" + "bb" * 32, log_index=5)
        events1 = self.parser.parse_logs([log], self.timestamps)
        events2 = self.parser.parse_logs([log], self.timestamps)
        assert len(events1) == 1
        assert len(events2) == 0  # Deduplicated

    def test_dedup_key_format(self):
        log = _make_log(tx_hash="0x" + "cc" * 32, log_index=3)
        events = self.parser.parse_logs([log], self.timestamps)
        assert events[0].dedup_key == "0x" + "cc" * 32 + ":3"

    def test_skip_exchange_taker(self):
        """Events where taker == exchange address are summary events -> filtered."""
        log = _make_log(taker=CTF_EXCHANGE)
        events = self.parser.parse_logs([log], self.timestamps)
        assert len(events) == 0

    def test_skip_neg_risk_exchange_taker(self):
        log = _make_log(address=NEG_RISK_CTF_EXCHANGE, taker=NEG_RISK_CTF_EXCHANGE)
        events = self.parser.parse_logs([log], self.timestamps)
        assert len(events) == 0

    def test_multiple_logs_different_indices(self):
        log1 = _make_log(log_index=0)
        log2 = _make_log(log_index=1)
        events = self.parser.parse_logs([log1, log2], self.timestamps)
        assert len(events) == 2

    def test_mark_seen(self):
        self.parser.mark_seen("0xabc:0")
        log = _make_log(tx_hash="0xabc" + "0" * 61, log_index=0)
        # The dedup_key won't match unless it's literally the same
        # mark_seen is for loading from DB
        events = self.parser.parse_logs([log], self.timestamps)
        # It should parse since dedup_key is different
        assert len(events) == 1
