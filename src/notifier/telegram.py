"""Telegram notification sender for trade alerts."""

from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends trade notifications to a Telegram chat via Bot API."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self.enabled:
            self._session = aiohttp.ClientSession()
            logger.info("Telegram notifier enabled (chat_id=%s)", self._chat_id)
        else:
            logger.info("Telegram notifier disabled (missing bot_token or chat_id)")

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def notify_status(self, message: str) -> None:
        """Send a plain status notification. Never raises — failures are logged."""
        if not self.enabled or not self._session:
            return
        try:
            await self._send(message)
        except Exception:
            logger.exception("Failed to send Telegram status notification")

    async def notify_trade(
        self,
        signal: object,
        trade: object,
        result: object,
    ) -> None:
        """Send a trade notification. Never raises — failures are logged."""
        if not self.enabled or not self._session:
            return

        try:
            text = self._format_message(signal, trade, result)
            await self._send(text)
        except Exception:
            logger.exception("Failed to send Telegram notification")

    def _format_message(
        self,
        signal: object,
        trade: object,
        result: object,
    ) -> str:
        from src.models.signals import TradeSignal
        from src.models.trades import CopyTrade, TradeResult

        sig: TradeSignal = signal  # type: ignore[assignment]
        t: CopyTrade = trade  # type: ignore[assignment]
        r: TradeResult = result  # type: ignore[assignment]

        is_filled = r.status.value in ("FILLED", "PARTIALLY_FILLED")
        side = "SELL" if sig.is_sell else "BUY"
        whale_short = f"{sig.whale_address[:6]}...{sig.whale_address[-4:]}"
        market = sig.market_question or "Unknown market"
        outcome = sig.outcome or "Unknown"

        if is_filled:
            header = f"\u2705 {side} {r.status.value}"
            lines = [
                f"<b>{header}</b>",
                "",
                f"\U0001f4ca Market: {market}",
                f"\U0001f3af Outcome: {outcome}",
            ]
            if sig.is_sell:
                lines.append(f"\U0001f4e6 Tokens sold: {r.filled_amount:.2f}")
            else:
                lines.append(f"\U0001f4b5 Amount: ${t.amount_usd:.2f}")
                if r.filled_price > 0:
                    lines.append(
                        f"\U0001f4e6 Filled: {r.filled_amount:.2f} shares @ ${r.filled_price:.4f}"
                    )
            lines.append(f"\U0001f40b Whale: {whale_short}")
        else:
            header = f"\u274c {side} {r.status.value}"
            lines = [
                f"<b>{header}</b>",
                "",
                f"\U0001f4ca Market: {market}",
                f"\U0001f3af Outcome: {outcome}",
            ]
            if sig.is_sell:
                lines.append(f"\U0001f4e6 Tokens sold: 0")
            else:
                lines.append(f"\U0001f4b5 Amount: ${t.amount_usd:.2f}")
            lines.append(f"\U0001f40b Whale: {whale_short}")
            if r.error_message:
                lines.append(f"\u26a0\ufe0f Error: {r.error_message}")

        return "\n".join(lines)

    async def _send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        timeout = aiohttp.ClientTimeout(total=10)
        async with self._session.request("POST", url, json=payload, timeout=timeout) as resp:  # type: ignore[union-attr]
            if resp.status != 200:
                body = await resp.text()
                logger.warning("Telegram API error %d: %s", resp.status, body)
