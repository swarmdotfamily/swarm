"""
What the swarm smells: the curve, turned into the five features the fly's
senses accept (flycoin/io_map.py), each in [0, 1].

  momentum   price now vs a short moving average, clipped
  liquidity  real quote reserve vs graduation threshold
  danger     sell share of recent volume
  crash      worst drop over the recent window
  social     how many flies are alive vs the cap (the colony smells itself)

This mapping is our editorial choice. The response is the connectome's.
"""
from collections import deque
import numpy as np


class Tape:
    def __init__(self, window=12):
        self.window = window
        self.price = deque(maxlen=window)
        self.buys = deque(maxlen=window)
        self.sells = deque(maxlen=window)
        self.liq = 0.0

    def push(self, price, buy_vol, sell_vol, liquidity_frac):
        self.price.append(float(price))
        self.buys.append(float(buy_vol))
        self.sells.append(float(sell_vol))
        self.liq = float(np.clip(liquidity_frac, 0.0, 1.0))

    def features(self, alive_frac=0.0):
        p = np.array(self.price, dtype=np.float64)
        if len(p) < 2:
            return {"momentum": 0.0, "liquidity": self.liq, "danger": 0.0,
                    "crash": 0.0, "social": float(np.clip(alive_frac, 0, 1))}
        ma = p[:-1].mean()
        mom = (p[-1] / ma - 1.0) if ma > 0 else 0.0
        momentum = float(np.clip(mom / 0.10, 0.0, 1.0))          # +10% over the window = full appetite
        peak = np.maximum.accumulate(p)
        dd = float(np.max((peak - p) / np.where(peak > 0, peak, 1.0)))
        crash = float(np.clip(dd / 0.25, 0.0, 1.0))               # -25% drawdown = full looming
        b, s = sum(self.buys), sum(self.sells)
        danger = float(np.clip(s / (b + s), 0.0, 1.0)) if (b + s) > 0 else 0.0
        return {"momentum": momentum, "liquidity": self.liq, "danger": danger,
                "crash": crash, "social": float(np.clip(alive_frac, 0, 1))}
