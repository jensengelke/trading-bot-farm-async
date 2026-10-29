"""
Message formatters for Telegram notifications.
Generates clean HTML-formatted strings for Telegram parse_mode='HTML'.
"""

from datetime import datetime
from typing import Optional
import pytz
from ib_async import Trade, Fill, Contract
from framework.telegram_client import escape_html


def _get_ny_now() -> str:
    tz = pytz.timezone("America/New_York")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def format_contract_summary(contract: Optional[Contract]) -> str:
    if not contract:
        return "Unknown Contract"
    sec_type = contract.secType or ""
    symbol = contract.symbol or ""
    if sec_type == "BAG":
        num = len(contract.comboLegs) if contract.comboLegs else 0
        return f"{symbol} Combo ({num} legs)"
    elif sec_type == "OPT":
        return f"{symbol} {contract.lastTradeDateOrContractMonth or ''} {contract.strike or ''}{contract.right or ''}"
    elif sec_type == "STK":
        return f"{symbol} (Stock)"
    elif sec_type == "FUT":
        return f"{symbol} Future {contract.lastTradeDateOrContractMonth or ''}"
    return f"{symbol} ({sec_type})"


def format_order_placement(trade: Trade, account: str = "") -> str:
    order = trade.order
    contract = trade.contract
    acct = account or getattr(order, "account", "") or ""
    lmt = getattr(order, "lmtPrice", None)
    price_str = f" @ {lmt:.2f}" if lmt is not None else ""
    order_ref = order.orderRef or "N/A"
    
    msg = [
        "🟢 <b>Order Submitted</b>",
        f"<b>Account:</b> <code>{escape_html(acct)}</code>",
        f"<b>Contract:</b> {escape_html(format_contract_summary(contract))}",
        f"<b>Action:</b> {order.action} {order.totalQuantity} {order.orderType}{price_str}",
        f"<b>Order ID:</b> <code>{order.orderId}</code>",
        f"<b>Order Ref:</b> <code>{escape_html(order_ref)}</code>",
    ]
    if contract.secType == "BAG" and contract.comboLegs:
        msg.append("<b>Legs:</b>")
        for i, leg in enumerate(contract.comboLegs, start=1):
            act = "BUY" if leg.action == "BUY" else "SELL"
            msg.append(f"  • #{i}: {act} {leg.ratio}x conId {leg.conId}")
    msg.append(f"<b>Time:</b> {escape_html(_get_ny_now())}")
    return "\n".join(msg)


def format_order_fill(trade: Trade, fill: Fill, account: str = "") -> str:
    order = trade.order
    contract = trade.contract
    exec_info = fill.execution
    acct = account or getattr(order, "account", "") or ""
    order_ref = order.orderRef or "N/A"
    status = trade.orderStatus.status if trade.orderStatus else "Filled"
    filled = trade.orderStatus.filled if trade.orderStatus else exec_info.shares
    total = (trade.orderStatus.remaining + filled) if trade.orderStatus else exec_info.shares
    
    comm_str = ""
    if fill.commissionReport and fill.commissionReport.commission is not None:
        comm = fill.commissionReport.commission
        curr = fill.commissionReport.currency or "USD"
        comm_str = f"\n<b>Commission:</b> {comm:.2f} {escape_html(curr)}"
        
    msg = [
        "🎯 <b>Order Executed</b>",
        f"<b>Account:</b> <code>{escape_html(acct)}</code>",
        f"<b>Contract:</b> {escape_html(format_contract_summary(contract))}",
        f"<b>Fill:</b> {exec_info.side} {exec_info.shares} @ {exec_info.price:.2f}",
        f"<b>Progress:</b> {filled} / {total} ({status})",
        f"<b>Order Ref:</b> <code>{escape_html(order_ref)}</code>",
        f"<b>Order ID:</b> <code>{order.orderId}</code>{comm_str}",
        f"<b>Time:</b> {escape_html(_get_ny_now())}"
    ]
    return "\n".join(msg)


def format_order_cancelled(trade: Trade, account: str = "", reason: str = "") -> str:
    order = trade.order
    acct = account or getattr(order, "account", "") or ""
    order_ref = order.orderRef or "N/A"
    msg = [
        "🔴 <b>Order Cancelled</b>",
        f"<b>Account:</b> <code>{escape_html(acct)}</code>",
        f"<b>Contract:</b> {escape_html(format_contract_summary(trade.contract))}",
        f"<b>Action:</b> {order.action} {order.totalQuantity} {order.orderType}",
        f"<b>Order ID:</b> <code>{order.orderId}</code>",
        f"<b>Order Ref:</b> <code>{escape_html(order_ref)}</code>"
    ]
    if reason:
        msg.append(f"<b>Reason:</b> {escape_html(reason)}")
    msg.append(f"<b>Time:</b> {escape_html(_get_ny_now())}")
    return "\n".join(msg)


def format_order_status_change(trade: Trade, old_status: str, account: str = "") -> str:
    order = trade.order
    acct = account or getattr(order, "account", "") or ""
    order_ref = order.orderRef or "N/A"
    new_status = trade.orderStatus.status if trade.orderStatus else "Unknown"
    msg = [
        "ℹ️ <b>Order Status Update</b>",
        f"<b>Account:</b> <code>{escape_html(acct)}</code>",
        f"<b>Contract:</b> {escape_html(format_contract_summary(trade.contract))}",
        f"<b>Status:</b> {escape_html(old_status)} ➔ <b>{escape_html(new_status)}</b>",
        f"<b>Filled:</b> {trade.orderStatus.filled} / {order.totalQuantity}",
        f"<b>Order ID:</b> <code>{order.orderId}</code>",
        f"<b>Order Ref:</b> <code>{escape_html(order_ref)}</code>",
        f"<b>Time:</b> {escape_html(_get_ny_now())}"
    ]
    return "\n".join(msg)


def _format_amount(val_str: any, currency: str = "") -> str:
    try:
        val = float(val_str)
        curr = f" {currency}" if currency else ""
        return f"{val:,.2f}{curr}"
    except (ValueError, TypeError):
        return f"{val_str} {currency}".strip()


def format_connected_readiness_message(
    bot_id: str,
    account: str,
    connection_name: str,
    account_summary: Optional[dict] = None,
    positions: Optional[list] = None,
    open_trades: Optional[list] = None
) -> str:
    lines = [
        "🟢 <b>Telegram Notifier Connected & Ready</b>",
        f"<b>Bot Instance:</b> <code>{escape_html(bot_id)}</code>",
        f"<b>Account:</b> <code>{escape_html(account or 'N/A')}</code>",
        f"<b>Connection:</b> <code>{escape_html(connection_name)}</code>",
        f"<b>Time:</b> {escape_html(_get_ny_now())}",
        ""
    ]

    # 1. Account Balance Section
    lines.append("💰 <b>Account Balances:</b>")
    if account_summary:
        tag_labels = [
            ("NetLiquidation", "Net Liquidation"),
            ("TotalCashValue", "Total Cash"),
            ("SettledCash", "Settled Cash"),
            ("AvailableFunds", "Available Funds"),
            ("BuyingPower", "Buying Power"),
            ("EquityWithLoanValue", "Equity w/ Loan"),
            ("GrossPositionValue", "Gross Position"),
            ("UnrealizedPnL", "Unrealized P&L"),
            ("RealizedPnL", "Realized P&L"),
            ("InitMarginReq", "Initial Margin"),
            ("MaintMarginReq", "Maint. Margin"),
        ]
        shown_any = False
        for tag, label in tag_labels:
            if tag in account_summary:
                item = account_summary[tag]
                val = item.get("value", "")
                curr = item.get("currency", "")
                lines.append(f"  • <b>{label}:</b> {_format_amount(val, curr)}")
                shown_any = True
        if not shown_any:
            for tag, item in list(account_summary.items())[:5]:
                lines.append(f"  • <b>{escape_html(tag)}:</b> {_format_amount(item.get('value', ''), item.get('currency', ''))}")
    else:
        lines.append("  • <i>Balance details not available</i>")
    lines.append("")

    # 2. Portfolio Positions Section
    pos_count = len(positions) if positions else 0
    lines.append(f"📊 <b>Portfolio Positions ({pos_count}):</b>")
    if positions:
        for pos in positions:
            contract = getattr(pos, "contract", None)
            contract_desc = format_contract_summary(contract) if contract else "Unknown"
            
            qty = getattr(pos, "position", 0)
            try:
                qty_float = float(qty)
                if qty_float > 0:
                    qty_str = f"+{int(qty_float)}" if qty_float.is_integer() else f"+{qty_float:.2f}"
                else:
                    qty_str = f"{int(qty_float)}" if qty_float.is_integer() else f"{qty_float:.2f}"
            except (ValueError, TypeError):
                qty_str = str(qty)

            avg_cost = getattr(pos, "avgCost", None)
            if avg_cost is None:
                avg_cost = getattr(pos, "averageCost", 0.0)
            try:
                avg_cost_str = f"{float(avg_cost):.2f}"
            except (ValueError, TypeError):
                avg_cost_str = str(avg_cost)

            mkt_price = getattr(pos, "marketPrice", None)
            unrealized_pnl = getattr(pos, "unrealizedPNL", None)
            
            extra = []
            if mkt_price is not None and mkt_price > 0:
                extra.append(f"Mkt: {mkt_price:.2f}")
            if unrealized_pnl is not None:
                pnl_sign = "+" if unrealized_pnl >= 0 else ""
                extra.append(f"P&L: {pnl_sign}${unrealized_pnl:,.2f}")
                
            extra_str = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"  • <b>{escape_html(contract_desc)}:</b> {qty_str} @ {avg_cost_str}{escape_html(extra_str)}")
    else:
        lines.append("  • <i>No open positions</i>")
    lines.append("")

    # 3. Open Orders Section
    open_count = len(open_trades) if open_trades else 0
    lines.append(f"📋 <b>Open Orders ({open_count}):</b>")
    if open_trades:
        for trade in open_trades:
            order = trade.order
            contract = trade.contract
            contract_desc = format_contract_summary(contract)
            action = order.action
            total_qty = order.totalQuantity
            order_type = order.orderType
            lmt = getattr(order, "lmtPrice", None)
            aux = getattr(order, "auxPrice", None)
            price_str = f" @ {lmt:.2f}" if lmt is not None else (f" aux {aux:.2f}" if aux is not None else "")
            status = trade.orderStatus.status if trade.orderStatus else "Active"
            ref_str = f" | Ref: <code>{escape_html(order.orderRef)}</code>" if order.orderRef else ""
            
            lines.append(
                f"  • <b>{action} {total_qty} {escape_html(contract_desc)}</b> "
                f"({order_type}{price_str}) [{escape_html(status)}]{ref_str}"
            )
    else:
        lines.append("  • <i>No open orders</i>")

    return "\n".join(lines)


def format_startup_message(
    bot_id: str,
    account: str,
    connection_name: str,
    account_summary: Optional[dict] = None,
    positions: Optional[list] = None,
    open_trades: Optional[list] = None
) -> str:
    return format_connected_readiness_message(
        bot_id=bot_id,
        account=account,
        connection_name=connection_name,
        account_summary=account_summary,
        positions=positions,
        open_trades=open_trades
    )
