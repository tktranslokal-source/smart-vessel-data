import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup

TELUK_LAMONG_URL = "https://app.teluklamong.co.id/webaccess/"
TPS_SURABAYA_URL = "https://webaccess.tps.co.id/webaccess/"
TZ = ZoneInfo("Asia/Jakarta")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    )
}
COLUMNS = [
    "RecordKey", "SourceWebsite", "Terminal", "VesselStatus",
    "VesselName", "VesselCode", "VoyageIn", "VoyageOut",
    "Service", "TradeType", "ShippingLine", "ATB", "ATD",
    "ETB", "ETD", "OpenStack", "ClosingTime", "ScrapeTimestamp"
]


def now_jakarta():
    return datetime.now(TZ)


def clean(value):
    return re.sub(r"\s+", " ", str(value)).strip()


def after_colon(value):
    return value.split(":", 1)[1].strip() if ":" in value else ""


def split_slash(value):
    parts = re.split(r"\s*/\s*", clean(value), maxsplit=1)
    return parts[0] if parts else "", parts[1] if len(parts) > 1 else ""


def parse_dt(value):
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(clean(value), fmt).replace(tzinfo=TZ)
        except ValueError:
            pass
    return None


def departed_last_24h(value):
    dt = parse_dt(value)
    if dt is None:
        return False
    now = now_jakarta()
    return now - timedelta(hours=24) <= dt <= now + timedelta(hours=1)


def fetch(url):
    response = requests.get(url, headers=HEADERS, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    lines = [clean(x) for x in text.splitlines() if clean(x)]
    return text, lines


def empty_record(**updates):
    record = {column: "" for column in COLUMNS}
    record.update(updates)
    return record


def parse_teluk_lamong():
    _, lines = fetch(TELUK_LAMONG_URL)
    stamp = now_jakarta().strftime("%Y-%m-%d %H:%M:%S")
    records = []
    for i, line in enumerate(lines):
        marker = line.upper()
        if not (marker.startswith("ATB") or marker.startswith("ETB")) or i < 5:
            continue
        vessel_line, voyage_line, service, trade, shipping = lines[i-5:i]
        if "/" not in vessel_line or "/" not in voyage_line:
            continue
        if trade.upper() not in ("DOMESTIC", "INTERNATIONAL"):
            continue
        vessel, code = split_slash(vessel_line)
        voyage_in, voyage_out = split_slash(voyage_line)
        details = {"ATB":"", "ATD":"", "ETB":"", "ETD":"", "OpenStack":"", "ClosingTime":""}
        for j, detail in enumerate(lines[i:min(len(lines), i+18)]):
            upper = detail.upper()
            if j > 0 and (upper.startswith("ATB") or upper.startswith("ETB")):
                break
            if upper.startswith("ATB"):
                details["ATB"] = after_colon(detail)
            elif upper.startswith("ATD"):
                details["ATD"] = after_colon(detail)
            elif upper.startswith("ETB"):
                details["ETB"] = after_colon(detail)
            elif upper.startswith("ETD"):
                details["ETD"] = after_colon(detail)
            elif "OPEN STACK" in upper:
                details["OpenStack"] = after_colon(detail)
            elif "CLOSING TIME CONTAINER" in upper:
                details["ClosingTime"] = after_colon(detail)
            elif "DETAIL CONTAINER" in upper:
                break
        if details["ATD"]:
            if not departed_last_24h(details["ATD"]):
                continue
            status = "Departed 24H"
        else:
            status = "Vessel Alongside" if marker.startswith("ATB") else "Vessel Schedule"
        key = re.sub(r"[^A-Z0-9-]", "", f"TL-{status}-{code}-{voyage_in}-{voyage_out}".upper())
        records.append(empty_record(
            RecordKey=key, SourceWebsite="Teluk Lamong", Terminal="TPK Teluk Lamong",
            VesselStatus=status, VesselName=vessel, VesselCode=code,
            VoyageIn=voyage_in, VoyageOut=voyage_out, Service=service,
            TradeType=trade.upper(), ShippingLine=shipping,
            ScrapeTimestamp=stamp, **details
        ))
    return pd.DataFrame(records, columns=COLUMNS)


def parse_tps():
    text, lines = fetch(TPS_SURABAYA_URL)
    stamp = now_jakarta().strftime("%Y-%m-%d %H:%M:%S")
    records = []
    # Alongside and schedule cards
    for i, line in enumerate(lines):
        marker = line.upper()
        if not (marker.startswith("ATB") or marker.startswith("ETA")) or i < 2:
            continue
        vessel_line, voyage_line = clean(lines[i-2]), clean(lines[i-1])
        if "/" not in voyage_line or ":" in vessel_line:
            continue
        vessel, code = vessel_line, ""
        m = re.match(r"^([A-Z0-9]+)\s*-\s*(.+)$", vessel, flags=re.I)
        if m:
            code, vessel = m.group(1).strip(), m.group(2).strip()
        voyage_in, voyage_out = split_slash(voyage_line)
        details = {"ATB":"", "ATD":"", "ETB":"", "ETD":"", "OpenStack":"", "ClosingTime":""}
        for j, detail in enumerate(lines[i:min(len(lines), i+15)]):
            upper = detail.upper()
            if j > 0 and (upper.startswith("ATB") or upper.startswith("ETA")):
                break
            if upper.startswith("ATB"):
                details["ATB"] = after_colon(detail)
            elif upper.startswith("ATD"):
                details["ATD"] = after_colon(detail)
            elif upper.startswith("ETA"):
                details["ETB"] = after_colon(detail)
            elif upper.startswith("ETD"):
                details["ETD"] = after_colon(detail)
            elif "OPEN STACK" in upper:
                details["OpenStack"] = after_colon(detail)
            elif "CLOSING TIME CONTAINER" in upper:
                details["ClosingTime"] = after_colon(detail)
        status = "Vessel Alongside" if marker.startswith("ATB") else "Vessel Schedule"
        key = re.sub(r"[^A-Z0-9-]", "", f"TPS-{status}-{vessel}-{voyage_in}-{voyage_out}".upper())
        records.append(empty_record(
            RecordKey=key, SourceWebsite="TPS Surabaya", Terminal="Terminal Petikemas Surabaya",
            VesselStatus=status, VesselName=vessel, VesselCode=code,
            VoyageIn=voyage_in, VoyageOut=voyage_out, TradeType="INTERNATIONAL",
            ScrapeTimestamp=stamp, **details
        ))
    # Departed list, regex across normalized full page text
    normalized = clean(text)
    pattern = re.compile(
        r"(.+?)\s*~\s*BERTH\s*:\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})\s*"
        r"~\s*DEPARTURE\s*:\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})",
        flags=re.I
    )
    for match in pattern.finditer(normalized):
        vessel = clean(match.group(1)).split(" Vessel Alongside")[-1].strip()
        atb, atd = match.group(2), match.group(3)
        if not vessel or not departed_last_24h(atd):
            continue
        key = re.sub(r"[^A-Z0-9-]", "", f"TPS-DEPARTED-{vessel}-{atd}".upper())
        records.append(empty_record(
            RecordKey=key, SourceWebsite="TPS Surabaya", Terminal="Terminal Petikemas Surabaya",
            VesselStatus="Departed 24H", VesselName=vessel, TradeType="INTERNATIONAL",
            ATB=atb, ATD=atd, ScrapeTimestamp=stamp
        ))
    return pd.DataFrame(records, columns=COLUMNS)


def main():
    teluk = parse_teluk_lamong()
    tps = parse_tps()
    data = pd.concat([teluk, tps], ignore_index=True)
    if data.empty:
        raise RuntimeError("Parser menghasilkan 0 vessel; data.json lama dipertahankan.")
    data = data.drop_duplicates(subset=["RecordKey"], keep="last").reset_index(drop=True)
    payload = {
        "lastUpdate": now_jakarta().strftime("%d/%m/%Y %H:%M:%S"),
        "totalRecords": int(len(data)),
        "records": data.fillna("").to_dict(orient="records")
    }
    with open("data.json", "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2, default=str)
    print(f"Teluk Lamong: {len(teluk)}")
    print(f"TPS Surabaya: {len(tps)}")
    print(f"Total: {len(data)}")


if __name__ == "__main__":
    main()
