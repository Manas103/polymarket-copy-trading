"""HTML template for the dashboard — single-file, no external deps."""

from __future__ import annotations

from datetime import datetime, timezone


def _fmt_uptime(secs: float) -> str:
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"


def _truncate(text: str, length: int = 50) -> str:
    if len(text) <= length:
        return text
    return text[:length - 3] + "..."


def _action_class(action: str) -> str:
    a = action.upper()
    if a.startswith("COPY_"):
        return "green"
    if "FAILED" in a or "ERROR" in a:
        return "red"
    return "yellow"


def _status_class(status: str) -> str:
    s = status.upper()
    if s == "FILLED":
        return "green"
    if s == "ERROR":
        return "red"
    return "yellow"


def _bar(value: float, max_val: float, width: int = 120) -> str:
    if max_val <= 0:
        return ""
    pct = min(value / max_val, 1.0)
    px = int(pct * width)
    return f'<span class="bar" style="width:{px}px"></span>'


def _bot_health(cursor_updated_at: str | None) -> tuple[str, str]:
    """Return (status_text, css_class) based on block cursor freshness."""
    if not cursor_updated_at:
        return "Never Started", "red"
    try:
        last_update = datetime.fromisoformat(cursor_updated_at)
        if last_update.tzinfo is None:
            last_update = last_update.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last_update).total_seconds()
        if age <= 120:
            return "Online", "green"
        return f"Offline ({int(age)}s stale)", "red"
    except (ValueError, TypeError):
        return "Unknown", "yellow"


def render_dashboard(data: dict) -> str:
    uptime = _fmt_uptime(data["uptime_secs"])
    last_block = data["last_block"] or "N/A"
    trade_count = data["trade_count"]
    max_trades = data["max_daily_trades"]
    total_spend = data["total_spend"]
    max_spend = data["max_daily_spend"]
    positions = data["open_positions"]
    max_pos = data["max_open_positions"]
    budget_remaining = max(0, max_spend - total_spend)

    # --- Health ---
    health_text, health_class = _bot_health(data.get("cursor_updated_at"))

    # --- Cards ---
    cards_html = f"""
    <div class="cards">
      <div class="card">
        <div class="card-label">Trades Today</div>
        <div class="card-value">{trade_count} / {max_trades}</div>
      </div>
      <div class="card">
        <div class="card-label">Spent Today</div>
        <div class="card-value">${total_spend:.2f} / ${max_spend:.2f}</div>
      </div>
      <div class="card">
        <div class="card-label">Open Positions</div>
        <div class="card-value">{len(positions)} / {max_pos}</div>
      </div>
      <div class="card">
        <div class="card-label">Budget Remaining</div>
        <div class="card-value">${budget_remaining:.2f}</div>
      </div>
    </div>"""

    # --- Open Positions table ---
    pos_rows = ""
    for p in positions:
        tid = p["token_id"][:12] + "..." if len(p["token_id"]) > 15 else p["token_id"]
        invested = p["total_invested_usd"]
        tokens = p["total_tokens"]
        avg = invested / tokens if tokens > 0 else 0
        updated = p["last_updated"]
        pos_rows += f"""<tr>
          <td class="mono">{tid}</td>
          <td>${invested:.2f}</td>
          <td>{tokens:.2f}</td>
          <td>${avg:.4f}</td>
          <td>{updated}</td>
        </tr>"""

    positions_html = f"""
    <h2>Open Positions</h2>
    <table>
      <tr><th>Token ID</th><th>Invested</th><th>Tokens</th><th>Avg Cost</th><th>Updated</th></tr>
      {pos_rows if pos_rows else '<tr><td colspan="5" class="empty">No open positions</td></tr>'}
    </table>"""

    # --- Recent Trades table ---
    trade_rows = ""
    for t in data["recent_trades"]:
        sc = _status_class(t["status"])
        question = _truncate(t.get("market_question", ""), 50)
        trade_rows += f"""<tr>
          <td>{t["created_at"]}</td>
          <td>{question}</td>
          <td>{t["side"]}</td>
          <td>${t["amount_usd"]:.2f}</td>
          <td class="{sc}">{t["status"]}</td>
          <td>{t["filled_price"]:.4f}</td>
        </tr>"""

    trades_html = f"""
    <h2>Recent Trades (last 20)</h2>
    <table>
      <tr><th>Time</th><th>Market</th><th>Side</th><th>Amount</th><th>Status</th><th>Fill Price</th></tr>
      {trade_rows if trade_rows else '<tr><td colspan="6" class="empty">No trades yet</td></tr>'}
    </table>"""

    # --- Signal Funnel ---
    signal_counts = data["signal_counts"]
    max_count = max(signal_counts.values()) if signal_counts else 1
    signal_rows = ""
    for action, count in sorted(signal_counts.items(), key=lambda x: -x[1]):
        signal_rows += f"""<tr>
          <td>{action}</td>
          <td>{count}</td>
          <td>{_bar(count, max_count)}</td>
        </tr>"""

    signal_html = f"""
    <h2>Signal Funnel (24h)</h2>
    <table>
      <tr><th>Action</th><th>Count</th><th></th></tr>
      {signal_rows if signal_rows else '<tr><td colspan="3" class="empty">No signals yet</td></tr>'}
    </table>"""

    # --- Active Accumulations ---
    accumulations = data.get("active_accumulations", [])
    fill_window = data.get("fill_window_seconds", 1800)
    acc_rows = ""
    for a in accumulations:
        whale_short = a["whale_address"][:6] + "..." + a["whale_address"][-4:] if len(a["whale_address"]) > 12 else a["whale_address"]
        tid = a["token_id"][:12] + "..." if len(a["token_id"]) > 15 else a["token_id"]
        # Compute time remaining from first fill
        try:
            first_dt = datetime.fromisoformat(a["first_fill"])
            if first_dt.tzinfo is None:
                first_dt = first_dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - first_dt).total_seconds()
            remaining = max(0, fill_window - age)
            remaining_str = f"{int(remaining // 60)}m {int(remaining % 60)}s"
        except (ValueError, TypeError):
            remaining_str = "?"
        acc_rows += f"""<tr>
          <td class="mono">{whale_short}</td>
          <td class="mono">{tid}</td>
          <td class="green" style="font-weight:bold;">${a["total_usd"]:.0f}</td>
          <td>{a["fill_count"]}</td>
          <td>{a["last_fill"]}</td>
          <td>{remaining_str}</td>
        </tr>"""

    acc_html = f"""
    <h2>Active Accumulations</h2>
    <table>
      <tr><th>Whale</th><th>Token</th><th>Accumulated</th><th>Fills</th><th>Last Fill</th><th>Window Left</th></tr>
      {acc_rows if acc_rows else '<tr><td colspan="6" class="empty">No active accumulations</td></tr>'}
    </table>"""

    # --- Whale Activity Feed ---
    whale_signals = data.get("whale_signals", [])
    whale_rows = ""
    for ws in whale_signals:
        ac = _action_class(ws["action"])
        whale_short = ws["whale_address"][:6] + "..." + ws["whale_address"][-4:] if len(ws["whale_address"]) > 12 else ws["whale_address"]
        question = _truncate(ws.get("market_question", ""), 40)
        whale_rows += f"""<tr>
          <td>{ws["created_at"]}</td>
          <td class="mono">{whale_short}</td>
          <td class="{ac}">{ws["action"]}</td>
          <td>{question}</td>
          <td>${ws["usd_amount"]:.0f}</td>
          <td>{ws.get("outcome", "")}</td>
        </tr>"""

    whale_html = f"""
    <h2>Whale Activity (recent 50)</h2>
    <table>
      <tr><th>Time</th><th>Whale</th><th>Action</th><th>Market</th><th>USD</th><th>Outcome</th></tr>
      {whale_rows if whale_rows else '<tr><td colspan="6" class="empty">No whale activity</td></tr>'}
    </table>"""

    # --- 7-Day Spend History ---
    history = data["spend_history"]
    max_day_spend = max((d["total_spend_usd"] for d in history), default=1) or 1
    history_rows = ""
    for d in history:
        history_rows += f"""<tr>
          <td>{d["date"]}</td>
          <td>{d["trade_count"]}</td>
          <td>${d["total_spend_usd"]:.2f}</td>
          <td>{_bar(d["total_spend_usd"], max_day_spend)}</td>
        </tr>"""

    history_html = f"""
    <h2>7-Day Spend History</h2>
    <table>
      <tr><th>Date</th><th>Trades</th><th>Spent</th><th></th></tr>
      {history_rows if history_rows else '<tr><td colspan="4" class="empty">No history</td></tr>'}
    </table>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="15">
<title>Polymarket Bot Monitor</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#1a1a2e; color:#e0e0e0; font-family:'Courier New',monospace; padding:20px; }}
  h1 {{ color:#00d4ff; margin-bottom:4px; font-size:1.4em; }}
  h2 {{ color:#00d4ff; margin:24px 0 8px; font-size:1.1em; }}
  .header {{ display:flex; justify-content:space-between; align-items:baseline; border-bottom:1px solid #333; padding-bottom:10px; margin-bottom:16px; flex-wrap:wrap; gap:8px; }}
  .header-info {{ color:#888; font-size:0.85em; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; }}
  .card {{ background:#16213e; border:1px solid #333; border-radius:6px; padding:14px; }}
  .card-label {{ color:#888; font-size:0.8em; text-transform:uppercase; }}
  .card-value {{ font-size:1.3em; margin-top:4px; color:#fff; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.85em; }}
  th {{ text-align:left; color:#888; border-bottom:1px solid #333; padding:6px 8px; }}
  td {{ padding:6px 8px; border-bottom:1px solid #222; }}
  .mono {{ font-family:'Courier New',monospace; }}
  .green {{ color:#00e676; }}
  .red {{ color:#ff5252; }}
  .yellow {{ color:#ffd740; }}
  .empty {{ color:#555; font-style:italic; text-align:center; }}
  .bar {{ display:inline-block; height:12px; background:#00d4ff; border-radius:2px; }}
</style>
</head>
<body>
  <div class="header">
    <h1>Polymarket Bot Monitor</h1>
    <span class="header-info"><span class="{health_class}" style="font-weight:bold;">&#9679; {health_text}</span> | Uptime: {uptime} | Last Block: {last_block} | Auto-refresh: 15s</span>
  </div>
  {cards_html}
  {positions_html}
  {trades_html}
  {signal_html}
  {acc_html}
  {whale_html}
  {history_html}
</body>
</html>"""
