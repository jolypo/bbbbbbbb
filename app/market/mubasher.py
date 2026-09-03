from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Optional

import httpx


@dataclass
class MubasherMarketTotals:
    volume: Optional[float]
    trading_value: Optional[float]
    index_value: Optional[float] = None
    change_percent: Optional[float] = None
    advancers: Optional[float] = None
    decliners: Optional[float] = None
    source: str = "MUBASHER"
    ok: bool = False
    reason: str = ""


class MubasherMarketTotalsClient:
    """Fetch a small market-wide snapshot from Mubasher.

    V12 used Mubasher only for total volume/value. V13 keeps those two fields
    sourced from Mubasher when available and can also use Mubasher as a safety
    fallback for TASI level/change when the primary provider returns a missing
    or zero market summary. Breadth is never fabricated: if Mubasher does not
    expose advancers/decliners, those fields remain unavailable.
    """

    def __init__(self, url: str, timeout_seconds: float = 15.0):
        self.url = str(url).strip()
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _parse_number(value: str | None, *, signed: bool = False) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip().replace("−", "-")
        if not text:
            return None
        allowed = r"[^0-9,\.\-+]" if signed else r"[^0-9,\.]"
        text = re.sub(allowed, "", text)
        if not text or text in {"+", "-", ".", "+.", "-."}:
            return None
        text = text.replace(",", "")
        try:
            return float(text)
        except ValueError:
            return None

    @classmethod
    def parse_from_html(cls, html: str) -> MubasherMarketTotals:
        text = unescape(str(html or ""))
        text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        def first(patterns: list[str], *, signed: bool = False, positive_only: bool = False) -> Optional[float]:
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.I | re.S)
                if not match:
                    continue
                value = cls._parse_number(match.group(1), signed=signed)
                if value is None:
                    continue
                if positive_only and value <= 0:
                    continue
                return value
            return None

        volume = first(
            [
                r"حجم\s+التداول\s*[:\-]?\s*([0-9][0-9,\.]{2,})",
                r"Volume\s*[:\-]?\s*([0-9][0-9,\.]{2,})",
            ],
            positive_only=True,
        )
        trading_value = first(
            [
                r"قيمة\s+التداول\s*[:\-]?\s*([0-9][0-9,\.]{2,})",
                r"Value\s+Traded\s*[:\-]?\s*([0-9][0-9,\.]{2,})",
                r"Trading\s+Value\s*[:\-]?\s*([0-9][0-9,\.]{2,})",
            ],
            positive_only=True,
        )

        # The Arabic market page places the TASI level and percentage directly
        # after "آخر تحديث" and before the Open/Previous/High/Low block.
        index_value = first(
            [
                r"آخر\s+تحديث\s*:?\s*.*?\s([0-9][0-9,]*\.[0-9]+)\s+[+\-−]?[0-9][0-9,\.]*\s+[+\-−]?[0-9][0-9,\.]*%\s+فتح",
                r"مؤشر\s+السوق\s+الرئيسية.*?\(TASI\).*?([0-9][0-9,]*\.[0-9]+)\s+[+\-−]?[0-9][0-9,\.]*\s+[+\-−]?[0-9][0-9,\.]*%",
            ],
            positive_only=True,
        )
        change_percent = first(
            [
                r"آخر\s+تحديث\s*:?\s*.*?\s[0-9][0-9,]*\.[0-9]+\s+[+\-−]?[0-9][0-9,\.]*\s+([+\-−]?[0-9][0-9,\.]*%)\s+فتح",
                r"مؤشر\s+السوق\s+الرئيسية.*?\(TASI\).*?[0-9][0-9,]*\.[0-9]+\s+[+\-−]?[0-9][0-9,\.]*\s+([+\-−]?[0-9][0-9,\.]*%)",
            ],
            signed=True,
        )

        # Some Mubasher page variants expose market breadth; use it only when
        # explicit labels are present. Otherwise leave it unavailable.
        advancers = first(
            [
                r"(?:الأسهم\s+)?(?:الصاعدة|المرتفعة)\s*[:\-]?\s*([0-9]{1,4})",
                r"Advancers\s*[:\-]?\s*([0-9]{1,4})",
            ],
            positive_only=True,
        )
        decliners = first(
            [
                r"(?:الأسهم\s+)?(?:الهابطة|المنخفضة)\s*[:\-]?\s*([0-9]{1,4})",
                r"Decliners\s*[:\-]?\s*([0-9]{1,4})",
            ],
            positive_only=True,
        )

        ok = any(
            value is not None
            for value in (volume, trading_value, index_value, change_percent, advancers, decliners)
        )
        return MubasherMarketTotals(
            volume=volume,
            trading_value=trading_value,
            index_value=index_value,
            change_percent=change_percent,
            advancers=advancers,
            decliners=decliners,
            ok=ok,
            reason="ok" if ok else "labels_not_found",
        )

    async def fetch(self) -> MubasherMarketTotals:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ar,en;q=0.8",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers=headers,
            ) as client:
                resp = await client.get(self.url)
                resp.raise_for_status()
                result = self.parse_from_html(resp.text)
                if not result.ok:
                    result.reason = "parse_failed"
                return result
        except Exception as exc:
            return MubasherMarketTotals(
                volume=None,
                trading_value=None,
                index_value=None,
                change_percent=None,
                advancers=None,
                decliners=None,
                ok=False,
                reason=str(exc),
            )
