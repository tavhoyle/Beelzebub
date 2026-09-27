import os
import json
import time
import ccxt
import pandas as pd

SYMBOL = "BTC/USD"
TIMEFRAME = "15m"

LIVE_ORDERS_ENABLED = False
PAPER_TRADING_ENABLED = True

MAX_RISK_USD = 2.00
MAX_ACCOUNT_ALLOCATION_USD = 13.54

STATE_FILE = "hoyle_state_combined.json"

exchange = ccxt.kraken({
    "apiKey": os.getenv("KRAKEN_API_KEY"),
    "secret": os.getenv("KRAKEN_API_SECRET"),
    "enableRateLimit": True
})


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "active": False,
        "side": None,
        "entry": 0,
        "stop": 0,
        "target": 0,
        "amount": 0,
        "entry_time": None,
        "paper_pnl": 0,
        "trades": 0,
        "wins": 0,
        "losses": 0
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, 1e-10)

    return 100 - (100 / (1 + rs))


def get_data():
    rows = exchange.fetch_ohlcv(
        SYMBOL,
        timeframe=TIMEFRAME,
        limit=100
    )

    df = pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        unit="ms"
    )

    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    df["rsi"] = rsi(df["close"])

    typical = (
        df["high"] +
        df["low"] +
        df["close"]
    ) / 3

    pv = typical * df["volume"]
    day = df["timestamp"].dt.date

    df["vwap"] = (
        pv.groupby(day).cumsum()
        /
        df["volume"].groupby(day).cumsum()
    )

    df["avg_volume"] = df["volume"].rolling(20).mean()

    return df


def position_size(entry, stop, usd):
    risk_per_btc = abs(entry - stop)

    if risk_per_btc <= 0:
        return 0

    risk_size = MAX_RISK_USD / risk_per_btc

    allocation = min(
        usd,
        MAX_ACCOUNT_ALLOCATION_USD
    )

    allocation_size = allocation / entry

    return min(
        risk_size,
        allocation_size
    )


def show_account():
    balance = exchange.fetch_balance()

    usd = float(balance["free"].get("USD", 0))
    btc = float(balance["free"].get("BTC", 0))

    print("ACCOUNT SAFETY")
    print(f"Available USD: {usd:.2f}")
    print(f"BTC Free: {btc:.8f}")
    print(f"BTC Total: {btc:.8f}")

    return usd, btc


def check_paper_position(state, candle):
    if not state["active"]:
        return state

    high = float(candle["high"])
    low = float(candle["low"])

    entry = state["entry"]
    stop = state["stop"]
    target = state["target"]
    amount = state["amount"]
    side = state["side"]

    print("")
    print(f"PAPER {side} ACTIVE")
    print(f"Entry: {entry:.2f}")
    print(f"Stop: {stop:.2f}")
    print(f"Target: {target:.2f}")
    print(f"Position Size: {amount:.8f} BTC")

    if side == "LONG":

        if low <= stop:
            pnl = (stop - entry) * amount

            state["paper_pnl"] += pnl
            state["trades"] += 1
            state["losses"] += 1
            state["active"] = False
            state["side"] = None

            print("")
            print("PAPER LONG STOPPED")
            print(f"Exit: {stop:.2f}")
            print(f"Paper P/L: ${pnl:.4f}")

        elif high >= target:
            pnl = (target - entry) * amount

            state["paper_pnl"] += pnl
            state["trades"] += 1
            state["wins"] += 1
            state["active"] = False
            state["side"] = None

            print("")
            print("PAPER LONG TARGET HIT")
            print(f"Exit: {target:.2f}")
            print(f"Paper P/L: ${pnl:.4f}")

        else:
            current = float(candle["close"])
            unrealized = (current - entry) * amount

            print(f"Current: {current:.2f}")
            print(f"Unrealized P/L: ${unrealized:.4f}")
            print("PAPER LONG: HOLD")

    elif side == "SHORT":

        if high >= stop:
            pnl = (entry - stop) * amount

            state["paper_pnl"] += pnl
            state["trades"] += 1
            state["losses"] += 1
            state["active"] = False
            state["side"] = None

            print("")
            print("PAPER SHORT STOPPED")
            print(f"Exit: {stop:.2f}")
            print(f"Paper P/L: ${pnl:.4f}")

        elif low <= target:
            pnl = (entry - target) * amount

            state["paper_pnl"] += pnl
            state["trades"] += 1
            state["wins"] += 1
            state["active"] = False
            state["side"] = None

            print("")
            print("PAPER SHORT TARGET HIT")
            print(f"Exit: {target:.2f}")
            print(f"Paper P/L: ${pnl:.4f}")

        else:
            current = float(candle["close"])
            unrealized = (entry - current) * amount

            print(f"Current: {current:.2f}")
            print(f"Unrealized P/L: ${unrealized:.4f}")
            print("PAPER SHORT: HOLD")

    save_state(state)

    return state


def main():
    exchange.load_markets()
    state = load_state()

    last_candle = None

    print("")
    print("HOYLE BOT STARTED")
    print("PAPER TRADING: ENABLED")
    print("LIVE ORDERS: DISABLED")
    print("Market:", SYMBOL)
    print("Timeframe:", TIMEFRAME)
    print("Paper LONG + SHORT execution: ENABLED")
    print("")

    while True:
        try:
            df = get_data()

            candle = df.iloc[-2]
            previous = df.iloc[-3]

            candle_time = str(candle["timestamp"])

            if candle_time == last_candle:
                time.sleep(10)
                continue

            last_candle = candle_time

            close = float(candle["close"])

            sma20 = float(candle["sma20"])
            sma50 = float(candle["sma50"])
            rsi14 = float(candle["rsi"])
            vwap = float(candle["vwap"])

            volume = float(candle["volume"])
            avg_volume = float(candle["avg_volume"])

            previous_high = float(previous["high"])
            previous_low = float(previous["low"])

            print("")
            print("=" * 60)
            print("HOYLE 20/50 COMBINED PAPER MONITOR")
            print("BTC/USD - 5 MINUTES")
            print("=" * 60)

            print("Completed Candle:", candle_time)
            print(f"Close: {close:.2f}")
            print(f"SMA20: {sma20:.2f}")
            print(f"SMA50: {sma50:.2f}")
            print(f"RSI14: {rsi14:.2f}")
            print(f"VWAP: {vwap:.2f}")

            usd, btc = show_account()

            state = check_paper_position(state, candle)
            save_state(state)

            if state["active"]:
                print("")
                print(f"Paper P/L Total: ${state['paper_pnl']:.4f}")
                print(f"Paper Trades: {state['trades']}")
                print(f"Wins: {state['wins']}")
                print(f"Losses: {state['losses']}")
                print("WAIT FOR NEXT CANDLE")
                continue

            long_trend = sma20 > sma50
            long_price = close > vwap
            long_rsi = rsi14 > 50
            long_breakout = close > previous_high
            long_volume = volume > avg_volume

            short_trend = sma20 < sma50
            short_price = close < vwap
            short_rsi = rsi14 < 50
            short_breakout = close < previous_low
            short_volume = volume > avg_volume

            long_signal = (
                long_trend
                and long_price
                and long_rsi
                and long_breakout
                and long_volume
            )

            short_signal = (
                short_trend
                and short_price
                and short_rsi
                and short_breakout
                and short_volume
            )

            print("")
            print("LONG CONDITIONS")
            print("Trend:", long_trend)
            print("Price > VWAP:", long_price)
            print("RSI > 50:", long_rsi)
            print("Breakout:", long_breakout)
            print("Volume:", long_volume)

            print("")
            print("SHORT CONDITIONS")
            print("Trend:", short_trend)
            print("Price < VWAP:", short_price)
            print("RSI < 50:", short_rsi)
            print("Breakout:", short_breakout)
            print("Volume:", short_volume)

            if long_signal and not short_signal:

                stop = close * 0.99665
                risk_distance = close - stop
                target = close + (risk_distance * 2)

                amount = position_size(
                    close,
                    stop,
                    usd
                )

                if amount > 0:

                    state["active"] = True
                    state["side"] = "LONG"
                    state["entry"] = close
                    state["stop"] = stop
                    state["target"] = target
                    state["amount"] = amount
                    state["entry_time"] = candle_time

                    save_state(state)

                    print("")
                    print("SIGNAL: LONG")
                    print(f"Entry: {close:.2f}")
                    print(f"Stop: {stop:.2f}")
                    print(f"Target 2R: {target:.2f}")
                    print(f"Position Size: {amount:.8f} BTC")
                    print(f"Position Value: ${amount * close:.2f}")
                    print(f"Maximum Risk: ${MAX_RISK_USD:.2f}")
                    print("")
                    print("PAPER LONG EXECUTED")
                    print("LIVE ORDER: BLOCKED")
                    print("No real order was sent to Kraken.")

                else:

                    print("")
                    print("LONG SIGNAL")
                    print("Paper trade NOT executed.")
                    print("Position size was too small.")

            elif short_signal and not long_signal:

                stop = close * 1.00335
                risk_distance = stop - close
                target = close - (risk_distance * 2)

                amount = position_size(
                    close,
                    stop,
                    usd
                )

                if amount > 0:

                    state["active"] = True
                    state["side"] = "SHORT"
                    state["entry"] = close
                    state["stop"] = stop
                    state["target"] = target
                    state["amount"] = amount
                    state["entry_time"] = candle_time

                    save_state(state)

                    print("")
                    print("SIGNAL: SHORT")
                    print(f"Entry: {close:.2f}")
                    print(f"Stop: {stop:.2f}")
                    print(f"Target 2R: {target:.2f}")
                    print(f"Position Size: {amount:.8f} BTC")
                    print(f"Position Value: ${amount * close:.2f}")
                    print(f"Maximum Risk: ${MAX_RISK_USD:.2f}")
                    print("")
                    print("PAPER SHORT EXECUTED")
                    print("LIVE ORDER: BLOCKED")
                    print("No real margin order was sent to Kraken.")

                else:

                    print("")
                    print("SHORT SIGNAL")
                    print("Paper trade NOT executed.")
                    print("Position size was too small.")

            elif long_signal and short_signal:

                print("")
                print("SIGNAL: CONFLICT")
                print("Both LONG and SHORT conditions triggered.")
                print("No trade opened.")

            else:

                print("")
                print("SIGNAL: WAIT")
                print("WAIT FOR NEXT CANDLE")
                print("No confirmed LONG or SHORT setup.")

            print("")
            print(f"Paper P/L Total: ${state['paper_pnl']:.4f}")
            print(f"Paper Trades: {state['trades']}")
            print(f"Wins: {state['wins']}")
            print(f"Losses: {state['losses']}")

        except Exception as e:

            print("")
            print("BOT ERROR:", str(e))
            print("Retrying in 15 seconds...")
            time.sleep(15)

        time.sleep(10)


if __name__ == "__main__":
    main()
