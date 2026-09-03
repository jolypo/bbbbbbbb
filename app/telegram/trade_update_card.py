from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def _font(size: int, bold: bool = False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def build_trade_update_card(trade, price: float, out_path: str, title: str = "TRADE UPDATE") -> str:
    """Generate one dynamic Telegram card from the same live trade payload.

    P/L in SAR is deliberately normalized to one share:
      current price - actual entry price.
    """
    entry = float((trade or {}).get("entry") or 0.0)
    current = float(price or 0.0)
    pnl_sar = current - entry
    pct = ((current - entry) / entry * 100.0) if entry else 0.0
    positive = pnl_sar >= 0

    bg = (12, 17, 19)
    accent = (64, 224, 140) if positive else (244, 92, 92)
    muted = (184, 191, 197)
    white = (245, 247, 249)
    gold = (217, 181, 112)

    img = Image.new("RGB", (1200, 675), bg)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((35, 35, 1165, 640), radius=34, outline=accent, width=5)

    d.text((600, 74), "ALLUQMANU_TASI", fill=gold, font=_font(38, True), anchor="ma")
    d.text((600, 138), title, fill=white, font=_font(33, True), anchor="ma")

    symbol = str((trade or {}).get("symbol") or "—")
    name = str((trade or {}).get("name") or "")
    d.text((600, 205), f"{symbol}  {name}", fill=muted, font=_font(28, True), anchor="ma")

    sign = "+" if pnl_sar >= 0 else ""
    d.text((600, 325), f"{sign}{pnl_sar:.2f} SAR", fill=accent, font=_font(82, True), anchor="mm")
    d.text((600, 410), f"{pct:+.2f}%  |  1 SHARE", fill=accent, font=_font(38, True), anchor="ma")

    d.text((250, 520), f"ENTRY  {entry:.2f}", fill=white, font=_font(28, True), anchor="ma")
    d.text((950, 520), f"CURRENT  {current:.2f}", fill=white, font=_font(28, True), anchor="ma")
    d.text((600, 590), "PROFIT" if positive else "LOSS", fill=accent, font=_font(30, True), anchor="ma")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG", optimize=True)
    return str(out)
