"""
Message formatters for DAX Spread Observation alerts and summaries.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import pytz
from ib_async import Trade, Contract
from framework.telegram_client import escape_html
from framework.formatters import format_contract_summary
from framework.option_spreads import OptionSpreadPosition, is_dax_trade


def _get_berlin_now() -> str:
    tz = pytz.timezone("Europe/Berlin")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def format_delta_breach_alert(
    spread: OptionSpreadPosition,
    short_delta: float,
    threshold: float
) -> str:
    """Format Telegram alert when short leg delta exceeds threshold."""
    spread_name = "Bear Call" if spread.right == "C" else "Bull Put"
    und_price_str = f"{spread.underlying_price:,.2f}" if spread.underlying_price else "N/A"
    buf_pts = spread.short_strike_buffer_points
    buf_str = f"{buf_pts:+.1f} pts" if buf_pts is not None else "N/A"
    buf_pct = spread.short_strike_buffer_percent
    buf_pct_str = f" ({buf_pct:+.2f}%)" if buf_pct is not None else ""

    lines = [
        "⚠️ <b>DAX Short Delta Alert</b>",
        f"<b>Strategy:</b> {spread_name} Spread (<code>{spread.quantity}x</code>)",
        f"<b>Expiry:</b> <code>{spread.expiration}</code>",
        f"<b>Short Strike:</b> <code>{spread.short_strike:.0f}{spread.right}</code>",
        f"<b>Long Strike:</b> <code>{spread.long_strike:.0f}{spread.right}</code> (Width: {spread.wing_width:.0f})",
        "",
        f"<b>Short Delta:</b> <b>{abs(short_delta):.3f}</b> (Threshold: {threshold:.3f})",
        f"<b>DAX Index:</b> {und_price_str}",
        f"<b>Strike Buffer:</b> {buf_str}{buf_pct_str}",
        f"<b>Time:</b> {escape_html(_get_berlin_now())}"
    ]
    return "\n".join(lines)


def format_strike_tested_alert(
    spread: OptionSpreadPosition,
    is_long: bool,
    strike: float,
    underlying_price: float,
    buffer_pts: float
) -> str:
    """Format Telegram alert when short or long strike is tested or breached."""
    spread_name = "Bear Call" if spread.right == "C" else "Bull Put"
    leg_type = "LONG STRIKE (Max Loss Zone)" if is_long else "SHORT STRIKE"
    alert_icon = "🚨" if is_long else "⚡"
    
    status_msg = "BREACHED" if buffer_pts <= 0 else "TESTED"
    sign_str = "beyond" if buffer_pts <= 0 else "away"
    
    lines = [
        f"{alert_icon} <b>DAX Strike {status_msg} Alert</b>",
        f"<b>Leg:</b> <b>{leg_type}</b>",
        f"<b>Strategy:</b> {spread_name} Spread (<code>{spread.quantity}x</code>)",
        f"<b>Expiry:</b> <code>{spread.expiration}</code>",
        f"<b>Strike:</b> <code>{strike:.0f}{spread.right}</code>",
        "",
        f"<b>DAX Index:</b> <b>{underlying_price:,.2f}</b>",
        f"<b>Distance:</b> {abs(buffer_pts):.1f} pts {sign_str}",
        f"<b>P&L:</b> {spread.unrealized_pnl:+,.2f} EUR",
        f"<b>Time:</b> {escape_html(_get_berlin_now())}"
    ]
    return "\n".join(lines)


def format_daily_spread_summary(
    spreads: List[OptionSpreadPosition],
    underlying_price: Optional[float] = None,
    account: str = "",
    open_trades: Optional[List[Trade]] = None,
    contract_map: Optional[Dict[int, Any]] = None
) -> str:
    """
    Format daily comprehensive status summary of all open DAX spreads:
    - Current unrealized profit
    - Whether a take profit order exists (with action, qty, price, and type)
    - Buffer between strike and underlying price
    - List of ALL open orders on the DAX index
    """
    total_pnl = sum(s.unrealized_pnl for s in spreads)
    und_str = f" | DAX: <b>{underlying_price:,.2f}</b>" if underlying_price else ""
    acct_str = f" (<code>{escape_html(account)}</code>)" if account else ""

    lines = [
        f"📊 <b>DAX Option Spreads Daily Summary</b>{acct_str}",
        f"<b>Positions:</b> {len(spreads)} open spreads{und_str}",
        f"<b>Total Unrealized P&L:</b> <b>{total_pnl:+,.2f} EUR</b>",
        f"<b>Time:</b> {escape_html(_get_berlin_now())}",
        ""
    ]

    if not spreads:
        lines.append("<i>No active DAX option spreads found.</i>")
    else:
        for i, s in enumerate(spreads, start=1):
            if s.underlying_price is None and underlying_price:
                s.underlying_price = underlying_price

            spread_type_short = "Call Spread" if s.right == "C" else "Put Spread"
            if s.has_take_profit:
                price_val = s.take_profit_price if s.take_profit_price is not None else 0.0
                act_str = f"{s.take_profit_action} " if s.take_profit_action else ""
                qty_str = f"{int(s.take_profit_qty or s.quantity)}x " if (s.take_profit_qty or s.quantity) else ""
                type_str = s.take_profit_type or "LMT"
                tif_str = f" {s.take_profit_tif}" if s.take_profit_tif else ""
                tp_status = f"✅ TP: {act_str}{qty_str}@ {price_val:.2f} ({type_str}{tif_str})"
            else:
                tp_status = "❌ No TP Order"
            
            delta_str = f"{abs(s.short_delta):.3f}" if s.short_delta is not None else "N/A"
            
            buf_pts = s.short_strike_buffer_points
            buf_pct = s.short_strike_buffer_percent
            if buf_pts is not None and buf_pct is not None:
                buf_str = f"{buf_pts:+.0f} pts ({buf_pct:+.1f}%)"
            else:
                buf_str = "N/A"

            lines.append(
                f"<b>{i}. {s.expiration} {spread_type_short} ({s.quantity}x)</b>\n"
                f"   • Strikes: <code>{s.short_strike:.0f}{s.right}</code> / <code>{s.long_strike:.0f}{s.right}</code>\n"
                f"   • Buffer: <b>{buf_str}</b> | short leg Δ: <code>{delta_str}</code>\n"
                f"   • P&L: <b>{s.unrealized_pnl:+,.2f} EUR</b>\n"
                f"   • Protection: {tp_status}"
            )
            lines.append("")

    # Section: ALL Open DAX Orders
    dax_trades = [t for t in (open_trades or []) if is_dax_trade(t)]
    lines.append(f"📋 <b>Open DAX Orders ({len(dax_trades)}):</b>")
    if dax_trades:
        cmap = contract_map or {}
        for t in dax_trades:
            o = t.order
            c = t.contract
            st = t.orderStatus.status if t.orderStatus else "Active"
            price = o.lmtPrice if o.lmtPrice not in (None, 0.0) else (o.auxPrice or 0.0)
            tif = f" {o.tif}" if o.tif else ""
            
            if c.secType == "BAG" and c.comboLegs:
                leg_descs = []
                exp = ""
                for leg in c.comboLegs:
                    opt_c = cmap.get(leg.conId)
                    if opt_c:
                        exp = getattr(opt_c, "lastTradeDateOrContractMonth", "")
                        leg_descs.append(f"{opt_c.strike:.0f}{opt_c.right}")
                    else:
                        leg_descs.append(f"conId {leg.conId}")
                legs_str = " / ".join(leg_descs)
                exp_str = f" ({exp})" if exp else ""
                desc = f"DAX Combo {legs_str}{exp_str}"
            else:
                desc = format_contract_summary(c)

            lines.append(
                f"  • <b>{o.action} {int(o.totalQuantity)}x {escape_html(desc)}</b> "
                f"@ {price:.2f} ({o.orderType}{tif}) [<code>{escape_html(st)}</code>]"
            )
    else:
        lines.append("  • <i>No open orders on DAX</i>")

    return "\n".join(lines)

