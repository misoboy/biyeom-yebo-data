#!/usr/bin/env python3
"""비염예보 데이터 빌드.

공공데이터포털 API 4종을 불러 시군구별로 묶은 정적 JSON(site/v1/latest.json)을 만든다.
GitHub Actions가 매시간 실행하고 GitHub Pages로 배포한다. 앱은 이 파일 하나만 받는다.

- 꽃가루농도위험지수(기상청, 참나무·소나무 3~6월 / 잡초류 8~10월): 06시·18시 발표분
- 대기오염 실시간(에어코리아, 전국 측정소 한 번에): 매시간
- 측정소 위치(에어코리아): 7일마다
- 단기예보 최저·최고기온(기상청): 하루 한 번(02시 발표분)

이전에 배포된 파일을 먼저 받아 두고, 이번에 실패한 부분은 이전 값을 그대로 쓴다.
외부 라이브러리 없이 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
REGIONS = json.loads((ROOT / "data" / "regions.json").read_text(encoding="utf-8"))
SITE = ROOT / "site"
PUBLISHED = os.environ.get("PUBLISHED_BASE", "https://misoboy.github.io/biyeom-yebo-data")
KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()

POLLEN_OPS = {
    "oak": ("getOakPollenRiskIdxV3", (3, 4, 5, 6)),
    "pine": ("getPinePollenRiskIdxV3", (3, 4, 5, 6)),
    # 기상청 API의 오퍼레이션 이름 자체가 'Riskndx'(오타)다. 고치면 NO_OPENAPI_SERVICE 가 난다.
    "weeds": ("getWeedsPollenRiskndxV3", (8, 9, 10)),
}
POLLEN_BASE = "https://apis.data.go.kr/1360000/HealthWthrIdxServiceV3/"
AIR_URL = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty"
STATION_URL = "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getMsrstnList"
FCST_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
AIR_FCST_URL = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMinuDustFrcstDspth"
AIR_GRADE = {"좋음": 1, "보통": 2, "나쁨": 3, "매우나쁨": 4}

# 에어코리아 대기질 예보 권역(19곳). 시도로 정해지지 않는 곳만 시군구 이름으로 나눈다.
# 경기 북부는 한강 북쪽(김포 포함)으로 잡았다. 남·북부 등급이 다른 날은 드물어 경계가 조금 틀려도 영향이 작다.
GYEONGGI_NORTH = ("의정부", "동두천", "고양", "구리", "남양주", "파주", "양주", "포천", "연천", "가평", "김포")
GANGWON_EAST = ("강릉", "동해", "태백", "속초", "삼척", "고성", "양양")
GWANGJU_GU = ("동구", "서구", "남구", "북구", "광산구")  # 전남광주통합특별시 안의 옛 광주광역시


def air_zone(region: dict) -> str:
    sido, name = region["sido"], region["name"]
    if sido.startswith("경기"):
        return "경기북부" if name.startswith(GYEONGGI_NORTH) else "경기남부"
    if sido.startswith("강원"):
        return "영동" if name.startswith(GANGWON_EAST) else "영서"
    if sido.startswith("전남") or sido.startswith("전라남"):
        return "광주" if name in GWANGJU_GU else "전남"
    for prefix, zone in (("서울", "서울"), ("부산", "부산"), ("대구", "대구"), ("인천", "인천"), ("광주", "광주"),
                         ("대전", "대전"), ("울산", "울산"), ("세종", "세종"), ("충청북", "충북"), ("충북", "충북"),
                         ("충청남", "충남"), ("충남", "충남"), ("전북", "전북"), ("전라북", "전북"),
                         ("경상북", "경북"), ("경북", "경북"), ("경상남", "경남"), ("경남", "경남"), ("제주", "제주")):
        if sido.startswith(prefix):
            return zone
    raise ValueError(f"no air forecast zone for {sido} {name}")
MAX_STATION_KM = 40.0

errors: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)


def call(url: str, params: dict, tries: int = 3, timeout: int = 45) -> dict:
    """공공데이터포털 JSON 호출. 키는 로그에 남기지 않는다."""
    q = urllib.parse.urlencode({"serviceKey": KEY, **params})
    last = ""
    for i in range(tries):
        try:
            with urllib.request.urlopen(f"{url}?{q}", timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
            if raw.lstrip().startswith("<"):
                # 게이트웨이 오류는 XML로 온다 (키 오류, 한도 초과 등)
                code = raw.split("<returnReasonCode>")[-1].split("<")[0] if "returnReasonCode" in raw else "?"
                last = f"gateway error {code}"
            else:
                return json.loads(raw)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as e:
            last = type(e).__name__
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{url.rsplit('/', 1)[-1]}: {last}")


def fetch_previous(name: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{PUBLISHED}/v1/{name}?t={int(time.time())}", timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # 첫 배포이거나 Pages가 잠깐 안 될 때
        log(f"previous {name} unavailable: {type(e).__name__}")
        return None


def to_int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))


# ---------- 꽃가루 ----------

def pollen_expected_issue(now: datetime) -> str:
    """지금 받을 수 있는 가장 최근 발표 시각(yyyyMMddHH). 발표 후 40분 여유."""
    t = now - timedelta(minutes=40)
    if t.hour >= 18:
        return t.strftime("%Y%m%d") + "18"
    if t.hour >= 6:
        return t.strftime("%Y%m%d") + "06"
    return (t - timedelta(days=1)).strftime("%Y%m%d") + "18"


def build_pollen(now: datetime, prev: dict | None) -> dict:
    month = now.month
    season = {k: month in months for k, (_, months) in POLLEN_OPS.items()}
    issue = pollen_expected_issue(now)
    prev_p = (prev or {}).get("pollen") or {}
    if not any(season.values()):
        return {"season": season, "issued": None, "areas": {}}
    if prev_p.get("issued") == issue and prev_p.get("season") == season and prev_p.get("areas"):
        log(f"pollen: {issue} already built, keep")
        return prev_p

    def one(region: dict, kind: str):
        op = POLLEN_OPS[kind][0]
        d = call(POLLEN_BASE + op, {"dataType": "JSON", "numOfRows": 10, "pageNo": 1,
                                    "areaNo": region["code"], "time": issue})
        hdr = d.get("response", {}).get("header", {})
        if hdr.get("resultCode") != "00":
            raise RuntimeError(f"{op} {region['code']}: {hdr.get('resultCode')} {hdr.get('resultMsg')}")
        items = d["response"]["body"]["items"]["item"]
        it = items[0] if isinstance(items, list) else items
        return [to_int(it.get(k)) for k in ("today", "tomorrow", "dayaftertomorrow", "todaysaftertomorrow")]

    areas: dict = {}
    prev_areas = prev_p.get("areas") or {}
    fails = 0
    jobs = [(r, k) for r in REGIONS for k, on in season.items() if on]
    with cf.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(one, r, k): (r, k) for r, k in jobs}
        for f in cf.as_completed(futs):
            r, k = futs[f]
            try:
                areas.setdefault(r["code"], {})[k] = f.result()
            except Exception as e:
                fails += 1
                old = (prev_areas.get(r["code"]) or {}).get(k)
                if old is not None:
                    areas.setdefault(r["code"], {})[k] = old
                if fails <= 3:
                    log(f"pollen fail: {e}")
    log(f"pollen: issued {issue}, {len(jobs) - fails}/{len(jobs)} ok")
    if fails == len(jobs):
        errors.append(f"pollen: all {fails} calls failed")
        return prev_p or {"season": season, "issued": None, "areas": {}}
    if fails:
        errors.append(f"pollen: {fails}/{len(jobs)} calls failed")
    return {"season": season, "issued": issue, "areas": areas}


# ---------- 측정소 ----------

def build_stations(now: datetime, prev: dict | None) -> dict:
    if prev and prev.get("updated"):
        age = now - datetime.fromisoformat(prev["updated"])
        if age < timedelta(days=7) and prev.get("stations"):
            return prev
    try:
        d = call(STATION_URL, {"returnType": "json", "numOfRows": 1000, "pageNo": 1})
        items = d["response"]["body"]["items"]
        st = []
        for it in items:
            # 에어코리아는 dmX 가 위도, dmY 가 경도다 (이름과 반대)
            lat, lon = to_float(it.get("dmX")), to_float(it.get("dmY"))
            if lat and lon:
                st.append({"name": it["stationName"], "lat": lat, "lon": lon, "addr": it.get("addr", "")})
        log(f"stations: {len(st)}")
        return {"updated": now.isoformat(timespec="seconds"), "stations": st}
    except Exception as e:
        errors.append(f"stations: {e}")
        return prev or {"updated": None, "stations": []}


def to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------- 대기질 ----------

def build_air(now: datetime, prev: dict | None, stations: list[dict]) -> dict:
    prev_a = (prev or {}).get("air") or {}
    try:
        d = call(AIR_URL, {"returnType": "json", "numOfRows": 1000, "pageNo": 1, "sidoName": "전국", "ver": "1.0"})
        items = d["response"]["body"]["items"]
    except Exception as e:
        errors.append(f"air: {e}")
        return prev_a
    by_name: dict[str, dict] = {}
    data_time = None
    for it in items:
        # 측정소 이름은 전국에서 겹치지 않는다(2026-11 확인, 674곳). 측정소 목록과 이름으로 맞춘다.
        rec = {"pm10": to_int(it.get("pm10Value")), "pm25": to_int(it.get("pm25Value")),
               "g10": to_int(it.get("pm10Grade")), "g25": to_int(it.get("pm25Grade")),
               "time": it.get("dataTime")}
        by_name.setdefault(it["stationName"], rec)
        if it.get("dataTime") and (data_time is None or it["dataTime"] > data_time):
            data_time = it["dataTime"]
    if not stations:
        errors.append("air: no station coordinates")
        return prev_a
    areas = {}
    for r in REGIONS:
        near = sorted(((km(r["lat"], r["lon"], s["lat"], s["lon"]), s) for s in stations), key=lambda x: x[0])
        out = {}
        for field, grade in (("pm10", "g10"), ("pm25", "g25")):
            for dist, s in near:
                if dist > MAX_STATION_KM:
                    break
                rec = by_name.get(s["name"])
                if rec and rec[field] is not None:
                    out[field] = rec[field]
                    out[grade] = rec[grade]
                    out.setdefault("st", s["name"])
                    out.setdefault("km", round(dist, 1))
                    break
        if out:
            areas[r["code"]] = out
    log(f"air: {data_time}, {len(areas)}/{len(REGIONS)} areas")
    return {"time": data_time, "areas": areas}


# ---------- 대기질 예보 (내일·모레) ----------

def parse_issue(data_time: str) -> str:
    """'2026-11-26 17시 발표' → '2026112617' (정렬용)"""
    digits = "".join(ch for ch in data_time if ch.isdigit())
    return digits[:10]


def parse_zone_grades(text: str) -> dict[str, int]:
    out = {}
    for part in (text or "").split(","):
        if ":" in part:
            zone, grade = (s.strip() for s in part.split(":", 1))
            if grade in AIR_GRADE:
                out[zone] = AIR_GRADE[grade]
    return out


def build_air_forecast(now: datetime, prev: dict | None) -> dict:
    """권역별 PM10·PM2.5 예보 등급을 시군구로 펼친다. 05·11·17·23시 발표, 17시 발표부터 모레까지 나온다."""
    prev_f = (prev or {}).get("airFcst") or {}
    days = [now.strftime("%Y-%m-%d")]
    if now.hour < 6:  # 05시 발표 전에는 어제 17·23시 발표분이 최신
        days.append((now - timedelta(days=1)).strftime("%Y-%m-%d"))
    items = []
    try:
        for day in days:
            d = call(AIR_FCST_URL, {"returnType": "json", "numOfRows": 100, "pageNo": 1, "searchDate": day})
            items += d["response"]["body"]["items"] or []
    except Exception as e:
        errors.append(f"airFcst: {e}")
        return prev_f
    # (종류, 대상일)마다 가장 최근 발표분만 쓴다
    latest: dict[tuple[str, str], tuple[str, dict[str, int]]] = {}
    for it in items:
        code, target = it.get("informCode"), (it.get("informData") or "").replace("-", "")
        if code not in ("PM10", "PM25") or len(target) != 8:
            continue
        issue = parse_issue(it.get("dataTime") or "")
        grades = parse_zone_grades(it.get("informGrade"))
        if grades and (key := (code, target)) and (key not in latest or issue > latest[key][0]):
            latest[key] = (issue, grades)
    if not latest:
        errors.append("airFcst: no forecast items")
        return prev_f
    today = now.strftime("%Y%m%d")
    targets = sorted({t for _, t in latest if t >= today})
    areas = {}
    for r in REGIONS:
        zone = air_zone(r)
        per_day = {}
        for t in targets:
            g10 = latest.get(("PM10", t), (None, {}))[1].get(zone)
            g25 = latest.get(("PM25", t), (None, {}))[1].get(zone)
            if g10 is not None or g25 is not None:
                per_day[t] = [g10, g25]
        if per_day:
            areas[r["code"]] = per_day
    issued = max(v[0] for v in latest.values())
    log(f"airFcst: issued {issued}, days {targets}, {len(areas)} areas")
    return {"issued": issued, "areas": areas}


# ---------- 기온 ----------

def build_temp(now: datetime, prev: dict | None) -> dict:
    prev_t = (prev or {}).get("temp") or {}
    base = now if now.hour >= 3 else now - timedelta(days=1)
    base_date = base.strftime("%Y%m%d")
    if prev_t.get("base") == base_date and prev_t.get("areas"):
        return prev_t
    grids = sorted({(r["nx"], r["ny"]) for r in REGIONS})

    def one(g):
        d = call(FCST_URL, {"dataType": "JSON", "numOfRows": 1500, "pageNo": 1, "base_date": base_date,
                            "base_time": "0200", "nx": g[0], "ny": g[1]})
        hdr = d["response"]["header"]
        if hdr.get("resultCode") != "00":
            raise RuntimeError(f"fcst {g}: {hdr.get('resultCode')} {hdr.get('resultMsg')}")
        days: dict[str, list] = {}
        for it in d["response"]["body"]["items"]["item"]:
            if it["category"] in ("TMN", "TMX"):
                slot = days.setdefault(it["fcstDate"], [None, None])
                slot[0 if it["category"] == "TMN" else 1] = to_float(it["fcstValue"])
        return [days.get((base + timedelta(days=i)).strftime("%Y%m%d"), [None, None]) for i in range(4)]

    by_grid = {}
    failed = []
    with cf.ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(one, g): g for g in grids}
        for f in cf.as_completed(futs):
            try:
                by_grid[futs[f]] = f.result()
            except Exception:
                failed.append(futs[f])
    # 응답이 큰 편(격자당 ~125KB)이라 가끔 시간 초과가 난다. 실패분만 한 번 더 천천히 받는다.
    still = []
    for g in failed:
        try:
            by_grid[g] = one(g)
        except Exception as e:
            still.append(g)
            log(f"temp fail: {e}")
    fails = len(still)
    log(f"temp: base {base_date}, {len(grids) - fails}/{len(grids)} grids ok (retried {len(failed)})")
    if fails == len(grids):
        errors.append(f"temp: all {fails} calls failed")
        return prev_t
    if fails:
        errors.append(f"temp: {fails}/{len(grids)} calls failed")
    prev_areas = prev_t.get("areas") or {}
    areas = {}
    for r in REGIONS:
        v = by_grid.get((r["nx"], r["ny"]))
        if v is None and prev_t.get("base") == base_date:
            v = prev_areas.get(r["code"])
        if v is not None:
            areas[r["code"]] = v
    return {"base": base_date, "areas": areas}


def main() -> int:
    if not KEY:
        log("DATA_GO_KR_KEY is not set")
        return 2
    now = datetime.now(KST)
    prev = fetch_previous("latest.json")
    prev_st = fetch_previous("stations.json")

    stations = build_stations(now, prev_st)
    out = {
        "v": 1,
        "generatedAt": now.isoformat(timespec="seconds"),
        "pollen": build_pollen(now, prev),
        "air": build_air(now, prev, stations.get("stations") or []),
        "airFcst": build_air_forecast(now, prev),
        "temp": build_temp(now, prev),
        "errors": errors,
        "source": "기상청, 한국환경공단(에어코리아) — 공공데이터포털. 에어코리아 실시간 자료는 미확정 자료입니다.",
    }
    (SITE / "v1").mkdir(parents=True, exist_ok=True)
    (SITE / "v1" / "latest.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (SITE / "v1" / "stations.json").write_text(json.dumps(stations, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (SITE / "v1" / "regions.json").write_text((ROOT / "data" / "regions.json").read_text(encoding="utf-8"), encoding="utf-8")
    (SITE / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>비염예보 데이터</title>"
        "<p>비염예보 앱용 공공데이터 묶음입니다. 출처: 기상청, 한국환경공단(에어코리아) — 공공데이터포털(공공누리 제1유형).</p>",
        encoding="utf-8")
    size = (SITE / "v1" / "latest.json").stat().st_size
    log(f"wrote latest.json {size} bytes, errors={errors}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### build {out['generatedAt']}\n- latest.json {size} bytes\n- errors: {errors or 'none'}\n")
    # 일부 지역만 실패한 건 이전 값으로 메꿨으니 넘어가고, 한 부분이 통째로 실패했을 때만 작업을 실패로 표시한다.
    critical = [e for e in errors if e.startswith(("air:", "stations:")) or "all " in e]
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as f:
        f.write(f"critical={len(critical)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
