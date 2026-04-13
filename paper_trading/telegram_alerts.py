"""
Paper Trading Telegram Alerts

Sends notifications to Telegram when paper trading data is collected.
Keeps you informed about what's happening in the background.
"""

import logging

logger = logging.getLogger("paper_trading.telegram")


class PaperTradingTelegramAlerts:
    """
    Sends paper trading notifications to Telegram.
    """

    def __init__(self, telegram_bot, chat_id: int):
        """
        Initialize with Telegram bot and chat ID.

        Args:
            telegram_bot: Existing Telegram bot instance
            chat_id: Chat ID to send alerts to
        """
        self.bot = telegram_bot
        self.chat_id = chat_id

    async def send_trade_fired_alert(
        self,
        station: str,
        city: str,
        strategy_id: str,
        strategy_name: str,
        confidence_score: int,
        assumed_entry_price: float,
        expected_value: float,
        hour: int,
    ) -> bool:
        """
        Send alert when a paper trading strategy fires a trade.

        Args:
            station: Station code (e.g., "KAUS")
            city: City name (e.g., "Austin")
            strategy_id: Strategy ID (e.g., "TIER2")
            strategy_name: Human-readable name
            confidence_score: Confidence score (0-100)
            assumed_entry_price: Entry price ($)
            expected_value: Expected value of trade ($)
            hour: Hour of day (for context)

        Returns:
            True if alert sent successfully
        """

        if not self.bot or not self.chat_id:
            logger.warning("Telegram bot or chat_id not configured")
            return False

        time_str = f"{hour:02d}:00" if hour < 24 else "unknown"

        message = (
            f"📊 <b>PAPER TRADE FIRED: {strategy_id}</b>\n"
            f"\n"
            f"<b>Strategy:</b> {strategy_name}\n"
            f"<b>Station:</b> {station} ({city})\n"
            f"<b>Time:</b> {time_str}\n"
            f"<b>Confidence:</b> {confidence_score}/100\n"
            f"<b>Entry Price:</b> ${assumed_entry_price:.2f}\n"
            f"<b>Expected Value:</b> ${expected_value:.4f}\n"
            f"\n"
            f"<i>This is a paper trade (shadow bot). "
            f"Your real bot is unaffected.</i>"
        )

        try:
            await self.bot.send_message(self.chat_id, message, parse_mode="HTML")
            logger.info(
                f"[{station}] {strategy_id} paper trade alert sent to Telegram"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send paper trade alert to Telegram: {e}")
            return False

    async def send_daily_summary_alert(
        self, date: str, summaries: dict, comparative: dict
    ) -> bool:
        """
        Send end-of-day summary to Telegram.

        Args:
            date: Date string (YYYY-MM-DD)
            summaries: Dict of {strategy_id: {trades_fired, win_rate, total_pnl, ...}}
            comparative: Comparison dict {tier1_pnl, tier2_pnl, winner, ...}

        Returns:
            True if alert sent successfully
        """

        if not self.bot or not self.chat_id:
            logger.warning("Telegram bot or chat_id not configured")
            return False

        # Build message
        message_parts = [
            f"📈 <b>PAPER TRADING DAILY SUMMARY: {date}</b>",
            "",
        ]

        # Add each strategy's results
        for strategy_id, summary in summaries.items():
            trades = summary.get("trades_fired", 0)
            win_rate = summary.get("win_rate", "N/A")
            pnl = summary.get("total_pnl", 0)
            avg_price = summary.get("average_entry_price", 0)

            message_parts.append(
                f"<b>{summary.get('strategy_name', strategy_id)}</b>"
            )
            message_parts.append(f"  Trades: {trades} | Win rate: {win_rate}")
            message_parts.append(f"  P&L: ${pnl:.2f} | Avg entry: ${avg_price:.2f}")
            message_parts.append("")

        # Add comparative analysis
        if comparative:
            winner = comparative.get("winner", "UNKNOWN")
            diff = comparative.get("difference", 0)

            message_parts.append("<b>COMPARISON</b>")
            message_parts.append(f"  Winner: {winner}")
            message_parts.append(f"  Difference: ${diff:.2f}")
            message_parts.append("")

        message_parts.append(
            "<i>Data collected by paper trading framework. "
            "Your real bot is unaffected.</i>"
        )

        message = "\n".join(message_parts)

        try:
            await self.bot.send_message(self.chat_id, message, parse_mode="HTML")
            logger.info("Paper trading daily summary sent to Telegram")
            return True
        except Exception as e:
            logger.error(f"Failed to send daily summary to Telegram: {e}")
            return False

    async def send_framework_initialized_alert(
        self, enabled_strategies: list
    ) -> bool:
        """
        Send alert when paper trading framework starts.

        Args:
            enabled_strategies: List of enabled strategy IDs

        Returns:
            True if alert sent successfully
        """

        if not self.bot or not self.chat_id:
            logger.warning("Telegram bot or chat_id not configured")
            return False

        strategies_str = ", ".join(enabled_strategies)

        message = (
            f"✅ <b>PAPER TRADING FRAMEWORK STARTED</b>\n"
            f"\n"
            f"<b>Strategies enabled:</b> {strategies_str}\n"
            f"\n"
            f"The shadow bot is now running in the background.\n"
            f"You will receive updates when trades are fired and daily summaries.\n"
            f"\n"
            f"<i>Your real bot is unaffected.</i>"
        )

        try:
            await self.bot.send_message(self.chat_id, message, parse_mode="HTML")
            logger.info("Paper trading initialization alert sent to Telegram")
            return True
        except Exception as e:
            logger.error(f"Failed to send initialization alert to Telegram: {e}")
            return False
