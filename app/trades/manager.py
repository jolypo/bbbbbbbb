from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.risk.levels import round_saudi_price


class TradeManager:
    def __init__(self, store, settings):
        self.store = store
        self.s = settings

    def _round_trip_cost_pct(self):
        return (
            2.0 * float(getattr(self.s, "fee_bps", 0.0))
            + 2.0 * float(getattr(self.s, "slippage_bps", 0.0))
        ) / 100.0

    def _leg_net_pct(self, entry, exit_price):
        gross = (float(exit_price) - float(entry)) / float(entry) * 100.0
        return gross - self._round_trip_cost_pct(), gross

    def _entry_expiry(self, trade):
        """Entry thesis must trigger in the same Saudi session for both horizons."""
        now = datetime.now(timezone.utc)
        try:
            tz = ZoneInfo(str(getattr(self.s, "timezone", "Asia/Riyadh")))
            local = now.astimezone(tz)
            end_text = str(getattr(self.s, "signal_window_end", "14:50"))
            hh, mm = [int(x) for x in end_text.split(":", 1)]
            expiry_local = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if expiry_local > local:
                return expiry_local.astimezone(timezone.utc).isoformat()
        except Exception:
            pass
        return (now + timedelta(minutes=max(1, int(getattr(self.s, "entry_wait_expiry_minutes", 180))))).isoformat()

    def _session_date(self, when=None):
        try:
            tz = ZoneInfo(str(getattr(self.s, "timezone", "Asia/Riyadh")))
            if isinstance(when, datetime):
                dt = when
            elif when:
                dt = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
            else:
                dt = datetime.now(timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(tz).date().isoformat()
        except Exception:
            return datetime.now(timezone.utc).date().isoformat()

    def mark_observed_session(self, symbol, when=None):
        """Persist distinct Saudi session dates actually observed for an open trade."""
        state = self.store.state()
        day = self._session_date(when)
        for trade in state.get("open_trades", []):
            if str(trade.get("symbol")) != str(symbol) or trade.get("status") != "OPEN":
                continue
            dates = list(dict.fromkeys(str(x) for x in (trade.get("session_dates") or []) if x))
            if day not in dates:
                dates.append(day)
                trade["session_dates"] = dates
                trade["sessions_held"] = len(dates)
                self.store.save_state(state)
            return len(dates)
        return 0

    def add(self, signal):
        state = self.store.state()
        trades = state["open_trades"]
        if len(trades) >= self.s.max_open_trades:
            return False

        trade = dict(signal) if isinstance(signal, dict) else signal.to_dict()
        symbol = str(trade.get("symbol", "")).strip()
        if not symbol:
            return False
        if any(str(x.get("symbol", "")) == symbol for x in trades):
            return False

        trade.update(
            {
                "status": "WAITING_ENTRY",
                "current_price": float(trade.get("current_price", trade["entry"])),
                "planned_entry": float(trade["entry"]),
                "actual_entry": None,
                "entry_time": None,
                "session_dates": [],
                "sessions_held": 0,
                "published_at": datetime.now(timezone.utc).isoformat(),
                "entry_expires_at": self._entry_expiry(trade),
                "trade_horizon": str(trade.get("trade_horizon", "intraday") or "intraday"),
                "max_profit_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "tp1_hit": False,
                "tp2_hit": False,
                "tp3_hit": False,
                "sl_hit": False,
                "profit_alerts_sent": [],
                "near_sl_warning_sent": False,
                "trailing_stop": None,
                "exit": None,
                "exit_time": None,
                "result": None,
                "result_pct": None,
                "gross_result_pct": None,
                "result_sar": None,
                "realized_result_pct": 0.0,
                "remaining_position_pct": 100.0,
                "partial_exits": [],
                "estimated_round_trip_cost_pct": self._round_trip_cost_pct(),
            }
        )
        trades.append(trade)
        self.store.save_state(state)
        return True


    def _actual_entry_rr(self, trade, price):
        price=float(price); sl=float(trade["sl"]); tp1=float(trade["tp1"])
        risk=price-sl
        return (tp1-price)/risk if risk>0 else -1.0

    def _max_entry_for_min_rr(self, trade):
        rr=max(0.0,float(getattr(self.s,"min_rr",1.8) or 0.0))
        return (float(trade["tp1"]) + rr*float(trade["sl"]))/(1.0+rr) if rr>0 else float(trade["entry_high"])

    def activate_entry(self, symbol, price, when=None, source="snapshot"):
        """Activate a WAITING_ENTRY trade only after a real observed touch."""
        state = self.store.state()
        for trade in state.get("open_trades", []):
            if str(trade.get("symbol")) != str(symbol) or trade.get("status") != "WAITING_ENTRY":
                continue
            low=float(trade["entry_low"]); high=float(trade["entry_high"]); price=float(price)
            if not (low <= price <= high):
                return trade, []
            if self._actual_entry_rr(trade, price) + 1e-9 < float(getattr(self.s,"min_rr",1.8)):
                return trade, []
            trade["status"]="OPEN"
            trade["entry"]=price
            trade["actual_entry"]=price
            trade["entry_time"]=str(when or datetime.now(timezone.utc).isoformat())
            trade["session_dates"]=[self._session_date(trade["entry_time"])]
            trade["sessions_held"]=1
            trade["entry_activation_source"]=source
            trade["actual_rr_tp1"]=round(self._actual_entry_rr(trade,price),2)
            trade["current_price"]=price
            trade["last_bar_checked_at"]=trade["entry_time"]
            self.store.save_state(state)
            return trade, ["ENTRY"]
        return None, []

    def activate_entry_bar(self, symbol, high, low, close, bar_time=None):
        """Conservative delayed-bar entry activation when an OHLC bar intersects the zone."""
        state=self.store.state()
        for trade in state.get("open_trades", []):
            if str(trade.get("symbol")) != str(symbol) or trade.get("status") != "WAITING_ENTRY":
                continue
            zlow=float(trade["entry_low"]); zhigh=float(trade["entry_high"]); high=float(high); low=float(low)
            if high < zlow or low > zhigh:
                return trade, []
            # Exact sequence is unknown inside OHLC. For a buy we use the upper
            # edge of the entry zone as a conservative fill, capped to the bar.
            max_rr_fill=self._max_entry_for_min_rr(trade)
            fill=min(zhigh, high, max_rr_fill)
            fill=max(zlow, fill)
            if fill > min(zhigh,high) or fill < zlow or self._actual_entry_rr(trade,fill) + 1e-9 < float(getattr(self.s,"min_rr",1.8)):
                return trade, []
            trade["status"]="OPEN"; trade["entry"]=fill; trade["actual_entry"]=fill
            trade["entry_time"]=str(bar_time or datetime.now(timezone.utc).isoformat())
            trade["session_dates"]=[self._session_date(trade["entry_time"])]
            trade["sessions_held"]=1
            trade["entry_activation_source"]="completed_bar"; trade["actual_rr_tp1"]=round(self._actual_entry_rr(trade,fill),2); trade["current_price"]=float(close)
            trade["last_bar_checked_at"]=trade["entry_time"]
            events=["ENTRY"]
            # If the same completed bar intersects both the entry zone and the
            # stop, intrabar order is unknowable. Use the conservative outcome:
            # assume the trade was filled and then stopped in that bar.
            effective_sl=float(trade.get("trailing_stop") or trade["sl"])
            if low <= effective_sl:
                remaining=float(trade.get("remaining_position_pct",100.0))
                if remaining>0:
                    self._realize_leg(trade,"SL",effective_sl,remaining)
                trade["sl_hit"]=True; trade["status"]="CLOSED_SL"
                trade["exit"]=effective_sl; trade["exit_time"]=trade["entry_time"]
                trade["result_pct"]=float(trade.get("realized_result_pct",0.0))
                trade["result_sar"]=None; trade["result"]="WIN" if trade["result_pct"]>0 else "LOSS"
                trade["bar_execution_assumption"]="ENTRY_THEN_STOP_CONSERVATIVE"
                events.append("SL")
                history=self.store.history(); history.append(dict(trade)); self.store.save_history(history)
                state["open_trades"]=[x for x in state.get("open_trades",[]) if x is not trade]
            self.store.save_state(state)
            return trade,events
        return None,[]

    def expire_waiting(self, symbol, reason="ENTRY_WINDOW_EXPIRED"):
        state=self.store.state()
        for trade in list(state.get("open_trades", [])):
            if str(trade.get("symbol")) != str(symbol) or trade.get("status") != "WAITING_ENTRY":
                continue
            trade["status"]="EXPIRED"; trade["result"]="MISSED_ENTRY"
            trade["exit_time"]=datetime.now(timezone.utc).isoformat(); trade["expiry_reason"]=reason
            history=self.store.history(); history.append(dict(trade)); self.store.save_history(history)
            state["open_trades"]=[x for x in state.get("open_trades",[]) if x is not trade]
            self.store.save_state(state)
            return dict(trade)
        return None

    def set_signal_message_ids(self, symbol, message_ids):
        """Persist the original Telegram signal message id per destination chat."""
        state = self.store.state()
        changed = False
        for trade in state.get("open_trades", []):
            if str(trade.get("symbol")) == str(symbol):
                trade["signal_message_ids"] = {str(k): int(v) for k, v in (message_ids or {}).items()}
                changed = True
                break
        if changed:
            self.store.save_state(state)
        return changed

    def remove_open(self, symbol):
        state = self.store.state()
        before = len(state.get("open_trades", []))
        state["open_trades"] = [
            x for x in state.get("open_trades", [])
            if str(x.get("symbol", "")) != str(symbol)
        ]
        changed = len(state["open_trades"]) != before
        if changed:
            self.store.save_state(state)
        return changed

    def _realize_leg(self, trade, key, price, allocation_pct):
        allocation_pct = max(0.0, min(float(allocation_pct), float(trade.get("remaining_position_pct", 100.0))))
        if allocation_pct <= 0:
            return
        net_leg, gross_leg = self._leg_net_pct(trade["entry"], price)
        weighted = net_leg * allocation_pct / 100.0
        trade["realized_result_pct"] = float(trade.get("realized_result_pct", 0.0)) + weighted
        trade["remaining_position_pct"] = max(0.0, float(trade.get("remaining_position_pct", 100.0)) - allocation_pct)
        trade.setdefault("partial_exits", []).append({
            "level": key,
            "price": float(price),
            "allocation_pct": allocation_pct,
            "gross_leg_pct": gross_leg,
            "net_leg_pct": net_leg,
            "weighted_result_pct": weighted,
            "time": datetime.now(timezone.utc).isoformat(),
        })

    def update(self, symbol, price):
        state = self.store.state()
        for trade in state["open_trades"]:
            if trade["symbol"] != symbol:
                continue
            if trade.get("status") != "OPEN":
                return trade, []

            entry = float(trade["entry"])
            price = float(price)
            current_net_pct, current_gross_pct = self._leg_net_pct(entry, price)
            trade["current_price"] = price
            trade["gross_result_pct"] = current_gross_pct
            trade["estimated_round_trip_cost_pct"] = self._round_trip_cost_pct()
            trade["max_profit_pct"] = max(float(trade.get("max_profit_pct", 0)), current_net_pct)
            trade["max_drawdown_pct"] = min(float(trade.get("max_drawdown_pct", 0)), current_net_pct)
            events = []

            effective_sl = float(trade.get("trailing_stop") or trade["sl"])
            if price <= effective_sl:
                remaining = float(trade.get("remaining_position_pct", 100.0))
                if remaining > 0:
                    self._realize_leg(trade, "SL", price, remaining)
                trade["sl_hit"] = True
                trade["status"] = "CLOSED_SL"
                trade["exit"] = price
                trade["exit_time"] = datetime.now(timezone.utc).isoformat()
                trade["result_pct"] = float(trade.get("realized_result_pct", 0.0))
                trade["result_sar"] = None
                trade["result"] = "WIN" if trade["result_pct"] > 0 else "LOSS"
                events.append("SL")
            else:
                allocations = {
                    "tp1": float(getattr(self.s, "tp1_percent", 30.0)),
                    "tp2": float(getattr(self.s, "tp2_percent", 30.0)),
                    "tp3": float(getattr(self.s, "tp3_percent", 40.0)),
                }
                for key in ("tp1", "tp2", "tp3"):
                    hit_key = f"{key}_hit"
                    if price >= float(trade[key]) and not trade.get(hit_key, False):
                        trade[hit_key] = True
                        allocation = allocations[key]
                        # TP3 closes any remainder so percentages cannot strand a position.
                        if key == "tp3":
                            allocation = float(trade.get("remaining_position_pct", allocation))
                        self._realize_leg(trade, key.upper(), price, allocation)
                        events.append(key.upper())

                if trade.get("tp3_hit") and trade.get("status") == "OPEN":
                    trade["status"] = "CLOSED_TP3"
                    trade["exit"] = price
                    trade["exit_time"] = datetime.now(timezone.utc).isoformat()
                    trade["result_pct"] = float(trade.get("realized_result_pct", 0.0))
                    trade["result_sar"] = None
                    trade["result"] = "WIN" if trade["result_pct"] > 0 else "LOSS"
                    events.append("CLOSE_TP3")

            if trade.get("status", "").startswith("CLOSED"):
                history = self.store.history()
                history.append(dict(trade))
                state["open_trades"] = [x for x in state["open_trades"] if x is not trade]
                self.store.save_history(history)

            self.store.save_state(state)
            return trade, events

        return None, []

    def update_bar(self, symbol, high, low, close, bar_time=None):
        """Reconcile a completed OHLC bar against an open long paper trade.

        If stop and target are both inside the same bar, execution order is
        unknowable from OHLC alone, so we use the conservative assumption: stop
        first. Target fills use the configured target price rather than the bar
        high; stop fills use the effective stop price.
        """
        state = self.store.state()
        for trade in state.get("open_trades", []):
            if str(trade.get("symbol")) != str(symbol):
                continue
            if trade.get("status") != "OPEN":
                return trade, []

            high = float(high)
            low = float(low)
            close = float(close)
            entry = float(trade["entry"])
            events = []

            high_net, _ = self._leg_net_pct(entry, high)
            low_net, _ = self._leg_net_pct(entry, low)
            trade["max_profit_pct"] = max(float(trade.get("max_profit_pct", 0)), high_net)
            trade["max_drawdown_pct"] = min(float(trade.get("max_drawdown_pct", 0)), low_net)
            trade["current_price"] = close
            trade["gross_result_pct"] = (close - entry) / entry * 100.0
            trade["estimated_round_trip_cost_pct"] = self._round_trip_cost_pct()
            if bar_time:
                trade["last_bar_checked_at"] = str(bar_time)

            effective_sl = float(trade.get("trailing_stop") or trade["sl"])
            pending_targets = [
                key for key in ("tp1", "tp2", "tp3")
                if not trade.get(f"{key}_hit", False) and high >= float(trade[key])
            ]

            # Conservative same-bar ordering when OHLC cannot reveal sequence.
            if low <= effective_sl:
                remaining = float(trade.get("remaining_position_pct", 100.0))
                if remaining > 0:
                    self._realize_leg(trade, "SL", effective_sl, remaining)
                trade["sl_hit"] = True
                trade["status"] = "CLOSED_SL"
                trade["exit"] = effective_sl
                trade["exit_time"] = str(bar_time or datetime.now(timezone.utc).isoformat())
                trade["result_pct"] = float(trade.get("realized_result_pct", 0.0))
                trade["result_sar"] = None
                trade["result"] = "WIN" if trade["result_pct"] > 0 else "LOSS"
                trade["bar_execution_assumption"] = (
                    "CONSERVATIVE_STOP_FIRST" if pending_targets else "STOP_ONLY"
                )
                events.append("SL")
            else:
                allocations = {
                    "tp1": float(getattr(self.s, "tp1_percent", 30.0)),
                    "tp2": float(getattr(self.s, "tp2_percent", 30.0)),
                    "tp3": float(getattr(self.s, "tp3_percent", 40.0)),
                }
                for key in ("tp1", "tp2", "tp3"):
                    hit_key = f"{key}_hit"
                    target = float(trade[key])
                    if high >= target and not trade.get(hit_key, False):
                        trade[hit_key] = True
                        allocation = allocations[key]
                        if key == "tp3":
                            allocation = float(trade.get("remaining_position_pct", allocation))
                        self._realize_leg(trade, key.upper(), target, allocation)
                        events.append(key.upper())

                if trade.get("tp3_hit") and trade.get("status") == "OPEN":
                    trade["status"] = "CLOSED_TP3"
                    trade["exit"] = float(trade["tp3"])
                    trade["exit_time"] = str(bar_time or datetime.now(timezone.utc).isoformat())
                    trade["result_pct"] = float(trade.get("realized_result_pct", 0.0))
                    trade["result_sar"] = None
                    trade["result"] = "WIN" if trade["result_pct"] > 0 else "LOSS"
                    events.append("CLOSE_TP3")

            if trade.get("status", "").startswith("CLOSED"):
                history = self.store.history()
                history.append(dict(trade))
                state["open_trades"] = [x for x in state.get("open_trades", []) if x is not trade]
                self.store.save_history(history)

            self.store.save_state(state)
            return trade, events

        return None, []

    def time_exit(self, symbol, price, reason="TIME_EXIT", when=None):
        """Close any remaining paper position at an observed price without calling it SL/TP."""
        state = self.store.state()
        for trade in list(state.get("open_trades", [])):
            if str(trade.get("symbol")) != str(symbol) or trade.get("status") != "OPEN":
                continue
            price = float(price)
            remaining = float(trade.get("remaining_position_pct", 100.0))
            if remaining > 0:
                self._realize_leg(trade, reason, price, remaining)
            trade["status"] = "CLOSED_TIME_EXIT"
            trade["exit"] = price
            trade["exit_time"] = str(when or datetime.now(timezone.utc).isoformat())
            trade["time_exit_reason"] = reason
            trade["result_pct"] = float(trade.get("realized_result_pct", 0.0))
            trade["result_sar"] = None
            trade["result"] = "WIN" if trade["result_pct"] > 0 else "LOSS"
            history = self.store.history(); history.append(dict(trade)); self.store.save_history(history)
            state["open_trades"] = [x for x in state.get("open_trades", []) if x is not trade]
            self.store.save_state(state)
            return dict(trade)
        return None

    def apply_trailing(self, trade, price, atr=None):
        if not trade or trade.get("status") != "OPEN":
            return False
        changed = False
        current = float(trade.get("trailing_stop") or trade["sl"])
        new_stop = current

        # Move to break-even after TP1 even when ATR trailing is disabled.
        if trade.get("tp1_hit") and getattr(self.s, "trailing_after_tp1_to_entry", True):
            new_stop = max(new_stop, float(trade["entry"]))

        # ATR trailing after TP2 is separately controlled by TRAILING_STOP_ENABLED.
        if (
            getattr(self.s, "trailing_stop_enabled", False)
            and trade.get("tp2_hit")
            and atr
            and atr > 0
        ):
            new_stop = max(new_stop, float(price) - float(atr) * self.s.trailing_after_tp2_atr)

        if new_stop > current:
            trade["trailing_stop"] = round_saudi_price(new_stop, "floor")
            changed = True
            # Persist immediately so a subsequent monitor tick cannot reload the
            # pre-trailing state before TradingService performs its later save.
            state = self.store.state()
            for item in state.get("open_trades", []):
                if item.get("symbol") == trade.get("symbol"):
                    item["trailing_stop"] = trade["trailing_stop"]
                    break
            self.store.save_state(state)
        return changed
