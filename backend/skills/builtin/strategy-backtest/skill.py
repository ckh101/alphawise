"""
Strategy Backtest Skill Implementation
策略回测引擎，支持MA交叉、MACD、RSI、布林带策略
纯Python向量化回测，单持仓模型
"""

import importlib
from abc import ABC, abstractmethod
from typing import Any

from harness.core.exceptions import SkillError, ValidationError
from harness.core.logger import get_logger

logger = get_logger(__name__)


# ==================== 数据结构 ====================

class Position:
    """持仓记录"""

    def __init__(self, symbol: str, entry_date: str, entry_price: float, shares: int):
        self.symbol = symbol
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.shares = shares
        self.exit_date: str | None = None
        self.exit_price: float | None = None
        self.is_closed = False

    @property
    def pnl(self) -> float:
        if self.exit_price is not None:
            return (self.exit_price - self.entry_price) * self.shares
        return 0.0

    @property
    def return_pct(self) -> float:
        if self.exit_price and self.entry_price:
            return (self.exit_price - self.entry_price) / self.entry_price * 100
        return 0.0

    def close(self, exit_date: str, exit_price: float):
        self.exit_date = exit_date
        self.exit_price = exit_price
        self.is_closed = True

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "entry_date": self.entry_date,
            "entry_price": round(self.entry_price, 2),
            "exit_date": self.exit_date,
            "exit_price": round(self.exit_price, 2) if self.exit_price else None,
            "shares": self.shares,
            "pnl": round(self.pnl, 2),
            "return_pct": round(self.return_pct, 2),
            "is_closed": self.is_closed,
        }


class Portfolio:
    """资金管理（单持仓模型）"""

    def __init__(self, initial_cash: float = 100000.0):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: list[Position] = []
        self.closed_positions: list[Position] = []
        self.equity_curve: list[dict] = []

    @property
    def open_position(self) -> Position | None:
        for p in self.positions:
            if not p.is_closed:
                return p
        return None

    def buy(self, symbol: str, date: str, price: float) -> Position | None:
        if self.open_position is not None:
            return None
        shares = int(self.cash / price)
        if shares <= 0:
            return None
        cost = shares * price
        self.cash -= cost
        pos = Position(symbol, date, price, shares)
        self.positions.append(pos)
        return pos

    def sell(self, date: str, price: float) -> Position | None:
        pos = self.open_position
        if pos is None:
            return None
        pos.close(date, price)
        self.cash += pos.shares * price
        self.positions.remove(pos)
        self.closed_positions.append(pos)
        return pos

    def record_equity(self, date: str, current_price: float):
        market_value = 0.0
        pos = self.open_position
        if pos:
            market_value = pos.shares * current_price
        self.equity_curve.append({
            "date": date,
            "equity": round(self.cash + market_value, 2)
        })

    @property
    def final_equity(self) -> float:
        if self.equity_curve:
            return self.equity_curve[-1]["equity"]
        return self.cash


# ==================== 策略基类 ====================

class Strategy(ABC):
    """策略抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def default_params(self) -> dict: ...

    @abstractmethod
    def generate_signals(self, bars: list[dict], params: dict) -> list[dict]:
        """
        生成交易信号

        Args:
            bars: K线数据列表 [{date, open, high, low, close, volume}]
            params: 策略参数

        Returns:
            等长信号列表 [{date, signal: "buy"|"sell"|"hold", price, reason}]
        """
        ...


# ==================== 均线交叉策略 ====================

class MACrossoverStrategy(Strategy):
    """均线交叉策略"""

    @property
    def name(self) -> str:
        return "ma_crossover"

    @property
    def description(self) -> str:
        return "均线交叉策略：短均线上穿长均线买入，下穿卖出"

    def default_params(self) -> dict:
        return {"short_window": 5, "long_window": 20}

    def generate_signals(self, bars: list[dict], params: dict) -> list[dict]:
        short_window = params.get("short_window", 5)
        long_window = params.get("long_window", 20)

        closes = [bar["close"] for bar in bars]
        signals = []

        for i, bar in enumerate(bars):
            if i < long_window - 1:
                signals.append({
                    "date": bar["date"], "signal": "hold",
                    "price": bar["close"], "reason": "数据不足"
                })
                continue

            short_ma = sum(closes[i - short_window + 1:i + 1]) / short_window
            long_ma = sum(closes[i - long_window + 1:i + 1]) / long_window

            prev_short_ma = sum(closes[i - short_window:i]) / short_window
            prev_long_ma = sum(closes[i - long_window:i]) / long_window

            # 金叉：短均线从下方穿越长均线
            if prev_short_ma <= prev_long_ma and short_ma > long_ma:
                signals.append({
                    "date": bar["date"], "signal": "buy",
                    "price": bar["close"], "reason": f"MA{short_window}上穿MA{long_window}，金叉"
                })
            # 死叉：短均线从上方穿越长均线
            elif prev_short_ma >= prev_long_ma and short_ma < long_ma:
                signals.append({
                    "date": bar["date"], "signal": "sell",
                    "price": bar["close"], "reason": f"MA{short_window}下穿MA{long_window}，死叉"
                })
            else:
                signals.append({
                    "date": bar["date"], "signal": "hold",
                    "price": bar["close"], "reason": "无交叉信号"
                })

        return signals


# ==================== MACD策略 ====================

class MACDStrategy(Strategy):
    """MACD策略"""

    @property
    def name(self) -> str:
        return "macd"

    @property
    def description(self) -> str:
        return "MACD策略：MACD线上穿信号线买入，下穿卖出"

    def default_params(self) -> dict:
        return {"fast_period": 12, "slow_period": 26, "signal_period": 9}

    def _calc_ema(self, data: list[float], period: int) -> list[float]:
        """计算EMA"""
        if not data:
            return []
        multiplier = 2 / (period + 1)
        ema = [data[0]]
        for price in data[1:]:
            ema.append((price * multiplier) + (ema[-1] * (1 - multiplier)))
        return ema

    def generate_signals(self, bars: list[dict], params: dict) -> list[dict]:
        fast_period = params.get("fast_period", 12)
        slow_period = params.get("slow_period", 26)
        signal_period = params.get("signal_period", 9)

        closes = [bar["close"] for bar in bars]
        signals = []

        # 计算MACD
        fast_ema = self._calc_ema(closes, fast_period)
        slow_ema = self._calc_ema(closes, slow_period)
        macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
        signal_line = self._calc_ema(macd_line, signal_period)

        warmup = slow_period + signal_period - 1

        for i, bar in enumerate(bars):
            if i < warmup:
                signals.append({
                    "date": bar["date"], "signal": "hold",
                    "price": bar["close"], "reason": "MACD预热期"
                })
                continue

            curr_macd = macd_line[i]
            curr_signal = signal_line[i]
            prev_macd = macd_line[i - 1]
            prev_signal = signal_line[i - 1]

            # MACD金叉
            if prev_macd <= prev_signal and curr_macd > curr_signal:
                signals.append({
                    "date": bar["date"], "signal": "buy",
                    "price": bar["close"], "reason": "MACD金叉"
                })
            # MACD死叉
            elif prev_macd >= prev_signal and curr_macd < curr_signal:
                signals.append({
                    "date": bar["date"], "signal": "sell",
                    "price": bar["close"], "reason": "MACD死叉"
                })
            else:
                signals.append({
                    "date": bar["date"], "signal": "hold",
                    "price": bar["close"], "reason": "无MACD交叉"
                })

        return signals


# ==================== RSI策略 ====================

class RSIStrategy(Strategy):
    """RSI超买超卖策略"""

    @property
    def name(self) -> str:
        return "rsi"

    @property
    def description(self) -> str:
        return "RSI策略：RSI低于超卖线买入，高于超买线卖出"

    def default_params(self) -> dict:
        return {"period": 14, "overbought": 70, "oversold": 30}

    def _calc_rsi(self, closes: list[float], period: int) -> list[float | None]:
        """计算RSI"""
        rsi_values: list[float | None] = [None] * period
        if len(closes) <= period:
            return [None] * len(closes)

        gains = []
        losses = []
        for i in range(1, period + 1):
            change = closes[i] - closes[i - 1]
            gains.append(max(0, change))
            losses.append(max(0, -change))

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))

        for i in range(period + 1, len(closes)):
            change = closes[i] - closes[i - 1]
            gain = max(0, change)
            loss = max(0, -change)
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period

            if avg_loss == 0:
                rsi_values.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_values.append(100 - (100 / (1 + rs)))

        # 补齐长度
        while len(rsi_values) < len(closes):
            rsi_values.append(None)

        return rsi_values

    def generate_signals(self, bars: list[dict], params: dict) -> list[dict]:
        period = params.get("period", 14)
        overbought = params.get("overbought", 70)
        oversold = params.get("oversold", 30)

        closes = [bar["close"] for bar in bars]
        rsi_values = self._calc_rsi(closes, period)
        signals = []

        was_oversold = False
        was_overbought = False

        for i, bar in enumerate(bars):
            rsi = rsi_values[i]

            if rsi is None:
                signals.append({
                    "date": bar["date"], "signal": "hold",
                    "price": bar["close"], "reason": "RSI预热期"
                })
                continue

            # 跟踪状态
            if rsi < oversold:
                was_oversold = True
            if rsi > overbought:
                was_overbought = True

            # 买入：RSI从超卖区回升
            if was_oversold and rsi > oversold:
                signals.append({
                    "date": bar["date"], "signal": "buy",
                    "price": bar["close"], "reason": f"RSI({rsi:.1f})从超卖区回升"
                })
                was_oversold = False
            # 卖出：RSI从超买区回落
            elif was_overbought and rsi < overbought:
                signals.append({
                    "date": bar["date"], "signal": "sell",
                    "price": bar["close"], "reason": f"RSI({rsi:.1f})从超买区回落"
                })
                was_overbought = False
            else:
                signals.append({
                    "date": bar["date"], "signal": "hold",
                    "price": bar["close"], "reason": f"RSI={rsi:.1f}"
                })

        return signals


# ==================== 布林带策略 ====================

class BollingerBandStrategy(Strategy):
    """布林带策略"""

    @property
    def name(self) -> str:
        return "bollinger_band"

    @property
    def description(self) -> str:
        return "布林带策略：价格触及下轨买入，触及上轨卖出"

    def default_params(self) -> dict:
        return {"period": 20, "num_std": 2.0}

    def generate_signals(self, bars: list[dict], params: dict) -> list[dict]:
        period = params.get("period", 20)
        num_std = params.get("num_std", 2.0)

        closes = [bar["close"] for bar in bars]
        signals = []

        for i, bar in enumerate(bars):
            if i < period - 1:
                signals.append({
                    "date": bar["date"], "signal": "hold",
                    "price": bar["close"], "reason": "布林带预热期"
                })
                continue

            window = closes[i - period + 1:i + 1]
            middle = sum(window) / period
            std = (sum((x - middle) ** 2 for x in window) / period) ** 0.5
            upper = middle + num_std * std
            lower = middle - num_std * std

            if bar["close"] <= lower:
                signals.append({
                    "date": bar["date"], "signal": "buy",
                    "price": bar["close"], "reason": f"价格触及布林带下轨({lower:.2f})"
                })
            elif bar["close"] >= upper:
                signals.append({
                    "date": bar["date"], "signal": "sell",
                    "price": bar["close"], "reason": f"价格触及布林带上轨({upper:.2f})"
                })
            else:
                signals.append({
                    "date": bar["date"], "signal": "hold",
                    "price": bar["close"], "reason": "价格在布林带内"
                })

        return signals


# ==================== 回测引擎 ====================

class BacktestEngine:
    """回测引擎"""

    def __init__(
        self,
        strategy: Strategy,
        bars: list[dict],
        params: dict,
        initial_cash: float = 100000.0
    ):
        self.strategy = strategy
        self.bars = bars
        self.params = params
        self.portfolio = Portfolio(initial_cash)

    def run(self, symbol: str) -> dict[str, Any]:
        """
        执行回测

        Args:
            symbol: 股票代码

        Returns:
            回测结果字典
        """
        logger.info(f"Running backtest: {symbol} with {self.strategy.name}")

        if not self.bars:
            return self._empty_result(symbol)

        # 生成信号
        signals = self.strategy.generate_signals(self.bars, self.params)

        # 遍历信号执行交易
        for i, signal in enumerate(signals):
            bar = self.bars[i]
            self.portfolio.record_equity(bar["date"], bar["close"])

            if signal["signal"] == "buy" and self.portfolio.open_position is None:
                self.portfolio.buy(symbol, bar["date"], bar["close"])
            elif signal["signal"] == "sell" and self.portfolio.open_position is not None:
                self.portfolio.sell(bar["date"], bar["close"])

        # 收盘：强制平仓
        if self.portfolio.open_position and self.bars:
            last_bar = self.bars[-1]
            self.portfolio.sell(last_bar["date"], last_bar["close"])

        # 最终净值快照
        if self.bars:
            self.portfolio.record_equity(self.bars[-1]["date"], self.bars[-1]["close"])

        # 计算指标
        metrics = self._compute_metrics()

        # 构建结果
        trades = [p.to_dict() for p in self.portfolio.closed_positions]

        return {
            "strategy": self.strategy.name,
            "strategy_description": self.strategy.description,
            "symbol": symbol,
            "params": self.params,
            "start_date": self.bars[0]["date"] if self.bars else "",
            "end_date": self.bars[-1]["date"] if self.bars else "",
            "initial_cash": self.portfolio.initial_cash,
            "final_equity": round(self.portfolio.final_equity, 2),
            "metrics": metrics,
            "trades": trades,
            "equity_curve": self.portfolio.equity_curve,
        }

    def _empty_result(self, symbol: str) -> dict:
        return {
            "strategy": self.strategy.name,
            "strategy_description": self.strategy.description,
            "symbol": symbol,
            "params": self.params,
            "start_date": "", "end_date": "",
            "initial_cash": self.portfolio.initial_cash,
            "final_equity": self.portfolio.initial_cash,
            "metrics": {},
            "trades": [],
            "equity_curve": [],
        }

    def _compute_metrics(self) -> dict[str, Any]:
        """计算回测指标"""
        closed = self.portfolio.closed_positions
        if not closed:
            return {
                "total_return": 0.0, "annualized_return": 0.0,
                "max_drawdown": 0.0, "sharpe_ratio": 0.0,
                "win_rate": 0.0, "total_trades": 0,
                "profit_trades": 0, "loss_trades": 0,
                "avg_profit_pct": 0.0, "avg_loss_pct": 0.0,
            }

        # 总收益率
        total_return = (self.portfolio.final_equity / self.portfolio.initial_cash - 1) * 100

        # 年化收益率
        trading_days = len(self.portfolio.equity_curve)
        if trading_days > 1:
            annualized_return = ((self.portfolio.final_equity / self.portfolio.initial_cash)
                                 ** (252 / trading_days) - 1) * 100
        else:
            annualized_return = 0.0

        # 最大回撤
        max_drawdown = self._calc_max_drawdown()

        # 夏普比率
        sharpe_ratio = self._calc_sharpe_ratio()

        # 胜率
        profit_trades = [t for t in closed if t.pnl > 0]
        loss_trades = [t for t in closed if t.pnl <= 0]
        win_rate = len(profit_trades) / len(closed) * 100 if closed else 0

        # 平均盈亏
        avg_profit = sum(t.return_pct for t in profit_trades) / len(profit_trades) if profit_trades else 0
        avg_loss = sum(t.return_pct for t in loss_trades) / len(loss_trades) if loss_trades else 0

        return {
            "total_return": round(total_return, 2),
            "annualized_return": round(annualized_return, 2),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "win_rate": round(win_rate, 1),
            "total_trades": len(closed),
            "profit_trades": len(profit_trades),
            "loss_trades": len(loss_trades),
            "avg_profit_pct": round(avg_profit, 2),
            "avg_loss_pct": round(avg_loss, 2),
        }

    def _calc_max_drawdown(self) -> float:
        """计算最大回撤"""
        curve = self.portfolio.equity_curve
        if not curve:
            return 0.0

        peak = curve[0]["equity"]
        max_dd = 0.0

        for point in curve:
            equity = point["equity"]
            if equity > peak:
                peak = equity
            dd = (equity / peak - 1) * 100
            if dd < max_dd:
                max_dd = dd

        return max_dd

    def _calc_sharpe_ratio(self) -> float:
        """计算夏普比率（无风险利率=0）"""
        curve = self.portfolio.equity_curve
        if len(curve) < 2:
            return 0.0

        # 日收益率
        returns = []
        for i in range(1, len(curve)):
            prev = curve[i - 1]["equity"]
            curr = curve[i]["equity"]
            if prev > 0:
                returns.append(curr / prev - 1)

        if not returns:
            return 0.0

        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_return = variance ** 0.5

        if std_return == 0:
            return 0.0

        return mean_return / std_return * (252 ** 0.5)


# ==================== 技能类 ====================

class StrategyBacktestSkill:
    """策略回测技能"""

    def __init__(self):
        self.description = "策略回测技能，支持MA交叉、MACD、RSI、布林带策略回测"
        self.name = "strategy-backtest"
        self._strategies: dict[str, type[Strategy]] = {
            "ma_crossover": MACrossoverStrategy,
            "macd": MACDStrategy,
            "rsi": RSIStrategy,
            "bollinger_band": BollingerBandStrategy,
        }
        logger.info("StrategyBacktestSkill initialized")

    def get_tool_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如600519.SH"},
                    "strategy": {
                        "type": "string",
                        "enum": list(self._strategies.keys()),
                        "description": "策略名称"
                    },
                    "params": {"type": "object", "description": "策略参数（可选）"},
                    "start_date": {"type": "string", "description": "回测开始日期 YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "回测结束日期 YYYY-MM-DD"},
                    "initial_cash": {"type": "number", "description": "初始资金，默认100000"}
                },
                "required": ["symbol", "strategy"]
            }
        }

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def list_strategies(self) -> list[dict]:
        """列出所有可用策略"""
        result = []
        for key, cls in self._strategies.items():
            instance = cls()
            result.append({
                "name": instance.name,
                "description": instance.description,
                "default_params": instance.default_params(),
            })
        return result

    def get_strategy_params(self, strategy_name: str) -> dict:
        """获取策略参数"""
        cls = self._strategies.get(strategy_name)
        if not cls:
            raise ValidationError(
                f"Unknown strategy: {strategy_name}",
                error_code="BT_001",
                details={"strategy": strategy_name, "available": list(self._strategies.keys())}
            )
        instance = cls()
        return {
            "name": instance.name,
            "description": instance.description,
            "params": instance.default_params(),
        }

    def run_backtest(
        self,
        symbol: str,
        strategy: str,
        params: dict | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        initial_cash: float = 100000.0
    ) -> dict[str, Any]:
        """
        执行回测

        Args:
            symbol: 股票代码
            strategy: 策略名称
            params: 策略参数（可选，使用默认值）
            start_date: 开始日期
            end_date: 结束日期
            initial_cash: 初始资金

        Returns:
            回测结果
        """
        logger.info(f"Backtest requested: {symbol} strategy={strategy}")

        # 验证策略
        strategy_cls = self._strategies.get(strategy)
        if not strategy_cls:
            raise ValidationError(
                f"Unknown strategy: {strategy}",
                error_code="BT_002",
                details={"strategy": strategy, "available": list(self._strategies.keys())}
            )

        strategy_instance = strategy_cls()

        # 合并参数
        final_params = strategy_instance.default_params()
        if params:
            final_params.update(params)

        # 获取K线数据
        bars = self._fetch_kline(symbol, start_date, end_date)
        if not bars:
            raise SkillError(
                f"No kline data for {symbol}",
                error_code="BT_003",
                details={"symbol": symbol}
            )

        logger.info(f"Fetched {len(bars)} bars for backtesting")

        # 执行回测
        engine = BacktestEngine(strategy_instance, bars, final_params, initial_cash)
        result = engine.run(symbol)

        logger.info(
            f"Backtest completed: {result['metrics'].get('total_trades', 0)} trades, "
            f"return={result['metrics'].get('total_return', 0):.2f}%"
        )

        return result

    def _fetch_kline(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None
    ) -> list[dict]:
        """获取K线数据"""
        try:
            spec = importlib.util.spec_from_file_location(
                "tdx_kline_skill",
                "skills/builtin/tdx-kline/skill.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.get_kline(symbol, "daily", start_date, end_date)
        except Exception as e:
            logger.error(f"Failed to fetch kline data: {e}")
            raise SkillError(
                f"Failed to fetch kline data: {e}",
                error_code="BT_004",
                details={"symbol": symbol, "error": str(e)}
            ) from e
