from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "telegram"


def _font(size: int, bold: bool = False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _cover(draw, box, fill=(16, 20, 21)):
    draw.rectangle(box, fill=fill)


def _center(draw, xy, text, size, fill, bold=True):
    draw.text(xy, str(text), fill=fill, font=_font(size, bold), anchor="mm")


def build_report_card(metrics: dict, out_path: str) -> str:
    """Render live report values over the user's approved TASI report design.

    The template is visual-only. Every date/count/P&L/table row is rebuilt from
    the same metrics used by the Telegram caption, preventing stale zero cards.
    """
    m = metrics or {}
    period = str(m.get("period", "daily")).lower()
    template = ASSET_DIR / ("weekly_report_template.png" if period == "weekly" else "daily_report_template.png")
    img = Image.open(template).convert("RGB")
    d = ImageDraw.Draw(img)

    gold = (232, 191, 108)
    green = (86, 218, 83)
    red = (235, 72, 64)
    muted = (205, 200, 189)
    bg = (17, 21, 22)

    total = int(m.get("total_trades", 0) or 0)
    wins = int(m.get("wins", 0) or 0)
    losses = int(m.get("losses", 0) or 0)
    waiting = int(m.get("waiting_entry", 0) or 0)
    settled = int(m.get("settled", wins + losses) or 0)
    win_rate = float(m.get("win_rate", 0.0) or 0.0)
    gross_win = float(m.get("gross_win", 0.0) or 0.0)
    gross_loss = float(m.get("gross_loss", 0.0) or 0.0)
    net = float(m.get("net", 0.0) or 0.0)
    gross_win_sar = float(m.get("gross_win_sar", 0.0) or 0.0)
    gross_loss_sar = float(m.get("gross_loss_sar", 0.0) or 0.0)
    net_sar = float(m.get("net_sar", 0.0) or 0.0)
    label = str(m.get("period_label", "—"))

    # Geometry differs slightly between the two approved templates.
    if period == "weekly":
        date_box=(145,118,435,166); nums_y=296; sub_y=353; table=(50,570,1225,665); bottom_y=775; sar_y=845
        xs=(190,492,788,1085)
    else:
        date_box=(130,125,425,176); nums_y=341; sub_y=392; table=(52,595,1223,708); bottom_y=801; sar_y=853
        xs=(195,490,787,1085)

    _cover(d, date_box, bg)
    _center(d, ((date_box[0]+date_box[2])//2, (date_box[1]+date_box[3])//2), label, 21, muted)

    # Four top cards: total, wins, losses, win rate.
    for i, x in enumerate(xs):
        half = 120 if i == 3 else 75
        _cover(d, (x-half, nums_y-42, x+half, nums_y+46), bg)
    _center(d, (xs[0], nums_y), total, 52, gold)
    _center(d, (xs[1], nums_y), wins, 52, green)
    _center(d, (xs[2], nums_y), losses, 52, red)
    _center(d, (xs[3], nums_y), f"{win_rate:.1f}%", 46, green if wins >= losses else red)

    # Small total-card line and W/L line.
    _cover(d, (90, sub_y-18, 300, sub_y+18), bg)
    _center(d, (195, sub_y), f"SETTLED {settled} | WAIT {waiting}", 15, muted, False)
    _cover(d, (1015, sub_y-18, 1160, sub_y+18), bg)
    _center(d, (1085, sub_y), f"W/L {wins}/{losses}", 16, muted, False)

    # Trade table body. Up to four live rows aligned to the template columns.
    _cover(d, table, (19, 23, 24))
    rows = list(m.get("rows") or [])[-4:]
    if not rows:
        _center(d, ((table[0]+table[2])//2, (table[1]+table[3])//2), "NO TRADES IN THIS PERIOD", 23, muted)
    else:
        positions = (90, 235, 405, 555, 705, 865, 1065)
        y = table[1] + 24
        for i, row in enumerate(rows, 1):
            vals = (
                str(i),
                str(row.get("symbol", "—")),
                "MULTI" if str(row.get("type", "")).lower().startswith("multi") else "DAILY",
                f"{float(row.get('entry',0) or 0):.2f}",
                f"{float(row.get('high',0) or 0):.2f}",
                f"{float(row.get('best_pct',0) or 0):+.2f}%",
                str(row.get("status", "—"))[:12],
            )
            for x, value in zip(positions, vals):
                _center(d, (x, y), value, 14, muted, value in {"WIN", "LOSS", "OPEN", "WAITING_ENTRY"})
            y += 23

    # Bottom cards: percentage performance + one-share SAR beneath.
    bx = (245, 650, 1040) if period == "weekly" else (245, 650, 1040)
    for x in bx:
        _cover(d, (x-110, bottom_y-35, x+115, bottom_y+44), bg)
    _center(d, (bx[0], bottom_y), f"+{gross_win:.2f}%", 38, green)
    _center(d, (bx[1], bottom_y), f"-{gross_loss:.2f}%", 38, red)
    _center(d, (bx[2], bottom_y), f"{net:+.2f}%", 38, green if net >= 0 else red)

    for x in bx:
        _cover(d, (x-110, sar_y-19, x+115, sar_y+20), bg)
    _center(d, (bx[0], sar_y), f"SAR +{gross_win_sar:.2f}", 18, green)
    _center(d, (bx[1], sar_y), f"SAR -{gross_loss_sar:.2f}", 18, red)
    _center(d, (bx[2], sar_y), f"SAR {net_sar:+.2f}", 18, gold if net_sar >= 0 else red)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG", optimize=True)
    return str(out)
