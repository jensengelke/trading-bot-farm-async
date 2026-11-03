"""
OptionOmega settings report generator.

Maps OptionOmega backtest settings to live runtime equivalents, ignored historical
settings, and rejected/unsupported features (Plan Section 5).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SettingDisposition:
    name: str
    status: str  # "mapped", "ignored", "phase2", "phase3", "unsupported"
    target_key: Optional[str] = None
    reason: str = ""


OO_SETTINGS_CATALOG: Dict[str, SettingDisposition] = {
    "start_date": SettingDisposition("start_date", "ignored", None, "Historical test interval not applicable to live trading"),
    "end_date": SettingDisposition("end_date", "ignored", None, "Historical test interval not applicable to live trading"),
    "date_preset": SettingDisposition("date_preset", "ignored", None, "Historical test interval not applicable to live trading"),
    "starting_funds": SettingDisposition("starting_funds", "ignored", None, "Actual broker account equity is used live"),
    "ignore_margin": SettingDisposition("ignore_margin", "ignored", None, "Cannot bypass broker buying power in live trading"),
    "commissions": SettingDisposition("commissions", "ignored", None, "Broker commissions recorded in fills.commission"),
    "fees": SettingDisposition("fees", "ignored", None, "Recorded in fills.commission"),
    "entry_slippage": SettingDisposition("entry_slippage", "ignored", "execution.max_adverse_price_change", "Slippage is measured, not configured"),
    "exit_slippage": SettingDisposition("exit_slippage", "ignored", None, "Soft exits chase from mid toward natural side"),
    "stop_slippage": SettingDisposition("stop_slippage", "ignored", None, "Stop slippage measured as delta between trigger and fill"),
    "close_on_test_completion": SettingDisposition("close_on_test_completion", "ignored", None, "Live runtime has no test completion date"),
    "cap_non_opening_profits": SettingDisposition("cap_non_opening_profits", "ignored", None, "Historical modeling; limit order may receive price improvement"),
    "require_two_consecutive_hits": SettingDisposition("require_two_consecutive_hits", "ignored", None, "Retrospective backtest rule"),
    "cap_non_opening_stops": SettingDisposition("cap_non_opening_stops", "ignored", None, "Historical modeling; stop losses cannot be capped"),
    "ignore_single_bar_sl": SettingDisposition("ignore_single_bar_sl", "ignored", None, "Retrospective backtest rule"),
}

OO_SETTINGS_CATALOG.update({
    "ticker": SettingDisposition("ticker", "mapped", "underlying.ticker", "Underlying ticker symbol"),
    "legs": SettingDisposition("legs", "mapped", "legs", "Multi-leg structure"),
    "exact_dte": SettingDisposition("exact_dte", "mapped", "use_exact_dte", "Require exact DTE match"),
    "round_strikes": SettingDisposition("round_strikes", "mapped", "round_strikes_to_nearest", "Round strikes to nearest multiple"),
    "quantity": SettingDisposition("quantity", "mapped", "allocation.quantity", "Fixed contract quantity"),
    "max_open_positions": SettingDisposition("max_open_positions", "mapped", "allocation.max_open_positions", "Durable max open virtual positions limit"),
    "frequency": SettingDisposition("frequency", "mapped", "entry.frequency", "Trading days / schedule"),
    "blackout_dates": SettingDisposition("blackout_dates", "mapped", "entry.blackout_dates", "Blackout date exclusions"),
    "entry_times": SettingDisposition("entry_times", "mapped", "entry.times", "Scheduled entry times"),
    "floating_entry": SettingDisposition("floating_entry", "mapped", "entry.floating_entry", "Floating entry window with latch"),
    "min_credit": SettingDisposition("min_credit", "mapped", "entry.min_credit_for_open", "Explicit min credit bound"),
    "max_credit": SettingDisposition("max_credit", "mapped", "entry.max_credit_for_open", "Explicit max credit bound"),
    "min_debit": SettingDisposition("min_debit", "mapped", "entry.min_debit_for_open", "Explicit min debit bound"),
    "max_debit": SettingDisposition("max_debit", "mapped", "entry.max_debit_for_open", "Explicit max debit bound"),
    "oo_min_entry_premium": SettingDisposition("oo_min_entry_premium", "mapped", "entry.option_omega_min_entry_premium", "OptionOmega side-agnostic min premium"),
    "oo_max_entry_premium": SettingDisposition("oo_max_entry_premium", "mapped", "entry.option_omega_max_entry_premium", "OptionOmega side-agnostic max premium"),
    "profit_target": SettingDisposition("profit_target", "mapped", "profit_target", "Broker GTC limit profit target"),
    "stop_loss": SettingDisposition("stop_loss", "mapped", "stop_loss", "Broker GTC stop / stop-limit exit"),
    "close_before_first_expiry": SettingDisposition("close_before_first_expiry", "mapped", "exit.close_full_position_before_first_leg_expires", "First-expiry full position close"),

    # Deferred / Unsupported
    "margin_allocation": SettingDisposition("margin_allocation", "phase2", None, "Account margin sizing deferred to Phase 2"),
    "prune_oldest": SettingDisposition("prune_oldest", "phase2", None, "Prune oldest deferred to Phase 2"),
    "trailing_stop": SettingDisposition("trailing_stop", "phase2", None, "Trailing stops deferred to Phase 2"),
    "spread_premium_selection": SettingDisposition("spread_premium_selection", "phase3", None, "Spread premium selection deferred to Phase 3"),
    "dix_gex": SettingDisposition("dix_gex", "unsupported", None, "No external signal provider planned"),
    "custom_csv_signals": SettingDisposition("custom_csv_signals", "unsupported", None, "Custom external CSV signals not supported"),
})


@dataclass
class SettingsReport:
    mapped: List[SettingDisposition] = field(default_factory=list)
    ignored: List[SettingDisposition] = field(default_factory=list)
    deferred: List[SettingDisposition] = field(default_factory=list)
    unsupported: List[SettingDisposition] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)

    @property
    def is_valid_live_config(self) -> bool:
        return len(self.unsupported) == 0 and len(self.unknown) == 0


def analyze_optionomega_settings(raw_settings: Dict[str, Any]) -> SettingsReport:
    """Analyze a dictionary of OptionOmega settings against the specification catalog."""
    report = SettingsReport()
    for key in raw_settings:
        if key in OO_SETTINGS_CATALOG:
            disp = OO_SETTINGS_CATALOG[key]
            if disp.status == "mapped":
                report.mapped.append(disp)
            elif disp.status == "ignored":
                report.ignored.append(disp)
            elif disp.status in ("phase2", "phase3"):
                report.deferred.append(disp)
            elif disp.status == "unsupported":
                report.unsupported.append(disp)
        else:
            report.unknown.append(key)
    return report

