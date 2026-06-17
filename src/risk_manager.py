"""
Risk manager — position sizing, stop-loss/take-profit, daily loss limits
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class PositionSizing:
    symbol: str
    quantity: int
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    position_value: float
    risk_amount: float        # maximum loss amount


class RiskManager:
    def __init__(self):
        self.max_position_pct = float(os.getenv("MAX_POSITION_PCT", 0.05))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", 0.07))
        self.take_profit_pct = float(os.getenv("TAKE_PROFIT_PCT", 0.20))
        self.max_open_positions = int(os.getenv("MAX_OPEN_POSITIONS", 10))
        self.daily_loss_limit_pct = float(os.getenv("DAILY_LOSS_LIMIT_PCT", 0.03))

        self.daily_pnl = 0.0
        self.open_positions: dict[str, dict] = {}

    def calculate_position_size(self, symbol: str, price: float, portfolio_value: float) -> PositionSizing:
        """Calculate an appropriate position size"""
        max_dollars = portfolio_value * self.max_position_pct
        quantity = int(max_dollars / price)

        if quantity == 0:
            quantity = 1  # buy at least 1 share

        stop_loss = round(price * (1 - self.stop_loss_pct), 2)
        take_profit = round(price * (1 + self.take_profit_pct), 2)
        position_value = quantity * price
        risk_amount = quantity * (price - stop_loss)

        return PositionSizing(
            symbol=symbol,
            quantity=quantity,
            entry_price=price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            position_value=position_value,
            risk_amount=risk_amount,
        )

    def can_open_position(self, symbol: str, portfolio_value: float) -> tuple[bool, str]:
        """Check whether opening a new position is allowed"""
        if len(self.open_positions) >= self.max_open_positions:
            return False, f"Maximum position count reached ({self.max_open_positions})"

        if symbol in self.open_positions:
            return False, f"{symbol} already held"

        daily_loss_limit = portfolio_value * self.daily_loss_limit_pct
        if self.daily_pnl < -daily_loss_limit:
            return False, f"Daily loss limit triggered (${-self.daily_pnl:.2f})"

        return True, "OK"

    def register_position(self, symbol: str, quantity: int, entry_price: float,
                          stop_loss: float, take_profit: float):
        self.open_positions[symbol] = {
            "quantity": quantity,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }

    def close_position(self, symbol: str, exit_price: float):
        if symbol not in self.open_positions:
            return
        pos = self.open_positions.pop(symbol)
        pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
        self.daily_pnl += pnl
        return pnl

    def check_exit_signals(self, symbol: str, current_price: float) -> tuple[bool, str]:
        """Check whether a stop-loss or take-profit has been triggered"""
        if symbol not in self.open_positions:
            return False, ""

        pos = self.open_positions[symbol]
        if current_price <= pos["stop_loss"]:
            return True, f"Stop-loss triggered (${current_price:.2f} <= ${pos['stop_loss']:.2f})"
        if current_price >= pos["take_profit"]:
            return True, f"Take-profit triggered (${current_price:.2f} >= ${pos['take_profit']:.2f})"

        return False, ""

    def reset_daily_pnl(self):
        self.daily_pnl = 0.0

    def summary(self) -> str:
        lines = [
            f"📊 Risk status:",
            f"  Daily PnL: ${self.daily_pnl:+.2f}",
            f"  Open positions: {len(self.open_positions)}/{self.max_open_positions}",
        ]
        for sym, pos in self.open_positions.items():
            lines.append(f"  {sym}: {pos['quantity']} shares @ ${pos['entry_price']:.2f} | stop ${pos['stop_loss']:.2f} | target ${pos['take_profit']:.2f}")
        return "\n".join(lines)
