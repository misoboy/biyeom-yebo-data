# biyeom-yebo-data

비염예보 앱이 받는 공공데이터 묶음. 매시 35분·55분에 Cloudflare Worker(`trigger/`)가 GitHub Actions를 깨우면, Actions가 공공데이터포털 API를 불러 시군구별 JSON을 만들고 GitHub Pages로 배포한다. 앱은 이 파일 하나만 받으므로 사용자가 늘어도 API 호출 수는 그대로다.

- 배포 주소: https://misoboy.github.io/biyeom-yebo-data/v1/latest.json
- 지역 목록: https://misoboy.github.io/biyeom-yebo-data/v1/regions.json (앱에도 같은 파일이 들어 있음)

## 데이터와 호출량

| 항목 | API | 갱신 | 하루 호출 | 개발계정 한도 |
|---|---|---|---|---|
| 꽃가루(참나무·소나무 3~6월, 잡초류 8~10월) | 기상청 보건기상지수(3.0) | 06·18시 발표분 | 시즌 중 약 1,000 | 10,000 |
| 미세먼지·초미세먼지 | 에어코리아 시도별 실시간(`sidoName=전국`) | 매시간(2회 시도) | 48 | 500 |
| 측정소 위치 | 에어코리아 측정소정보 | 7일마다 | 1 이하 | 500 |
| 미세먼지 예보(내일·모레) | 에어코리아 대기질 예보통보(19개 권역) | 매시간(오전 6시 전엔 전날분도) | 48~60 | 500 |
| 최저·최고기온 4일 | 기상청 단기예보(02시 발표) | 하루 1번 | 224(격자 수) | 10,000 |

## latest.json 형식

```json
{
  "v": 1,
  "generatedAt": "2026-11-26T12:35:00+09:00",
  "pollen": {"season": {"oak": true, "pine": true, "weeds": false}, "issued": "2026040206",
             "areas": {"1111000000": {"oak": [2, 3, 1, null], "pine": [1, 1, 0, 0]}}},
  "air": {"time": "2026-11-26 12:00", "areas": {"1111000000": {"pm10": 38, "g10": 2, "pm25": 19, "g25": 2, "st": "중구", "km": 0.8}}},
  "airFcst": {"issued": "2026112617", "areas": {"1111000000": {"20261127": [2, 2], "20261128": [3, 2]}}},
  "temp": {"base": "20261126", "areas": {"1111000000": [[5.0, 16.0], [9.0, 16.0], [3.0, 8.0], [-2.0, 8.0]]}},
  "errors": []
}
```

- 꽃가루 배열은 발표일 기준 [오늘, 내일, 모레, 글피], 값은 0 낮음 · 1 보통 · 2 높음 · 3 매우높음. 18시 발표분은 오늘 값이 비어(`null`) 있을 수 있다.
- 미세먼지 등급(g10, g25)은 1 좋음 · 2 보통 · 3 나쁨 · 4 매우나쁨. 지역 중심에서 40km 안의 가장 가까운 측정소 값을 쓴다.
- 미세먼지 예보는 권역(서울·경기북부·영동 등 19곳) 등급을 시군구에 펼친 것. 날짜별 [PM10 등급, PM2.5 등급]. 17시 발표분부터 모레가 들어온다. 경기 북부는 한강 북쪽(김포 포함)으로 잡았다.
- 기온 배열은 base 날짜부터 4일치 [최저, 최고].
- 일부 지역 호출이 실패하면 이전 배포본의 값을 그대로 둔다. 한 부분이 통째로 실패하면 작업이 실패로 표시되어 메일이 온다.

## 실행 방아쇠 (trigger/)

GitHub Actions의 `schedule` 트리거는 이 계정에서 매일 6~9시간씩 늦고, 매시간 예약은 아예 돌지 않았다(2026-11-26, `misoboy/misoboy`의 0시 예약이 05:46~09:01 UTC에 돈 기록으로 확인).
그래서 Cloudflare Workers 예약 실행(무료)이 매시 35·55분에 `workflow_dispatch`를 보낸다. 수동 실행은 바로 시작된다. 워크플로의 `schedule`은 보조로 남겨 두었다(늦게 돌아도 해가 없다).

- Worker: `biyeom-yebo-trigger` (Cloudflare 계정 misoboy, 공개 주소 꺼짐), 비밀 값 `GITHUB_TOKEN`
- `GITHUB_TOKEN`: fine-grained PAT `biyeom-yebo-trigger` — 이 저장소만, Actions 읽기·쓰기. **2027-11-26께 만료**되니 그 전에 다시 만들어 넣는다:
  ```bash
  cd trigger && npx wrangler login   # 브라우저 승인
  npx wrangler secret put GITHUB_TOKEN < ~/secrets/github/biyeom-dispatch-token.txt
  npx wrangler logout
  ```
- 토큰이 만료되거나 Worker가 멈추면 데이터 갱신이 멈춘다(앱은 마지막 값과 갱신 시각을 보여준다). 확인: `gh run list --event workflow_dispatch -L 5`
- 다시 배포: `cd trigger && npx wrangler deploy`

## 설정

- 저장소 Secret `DATA_GO_KR_KEY`: 공공데이터포털 일반 인증키(디코딩 키). 4개 API 모두 같은 계정으로 활용신청되어 있어야 한다.
- Pages 소스: GitHub Actions.
- 개발계정 활용기간은 승인일(2026-11-26)부터 24개월. 만료 전에 연장 신청해야 한다.

## 로컬 실행

```bash
DATA_GO_KR_KEY="$(cat ~/secrets/datagokr/service_key.txt)" python3 scripts/build.py   # site/ 에 결과
python3 -m unittest discover -s tests
```

## 주의

- 새로 만든 저장소에서 예약 실행이 처음 1시간 넘게 시작되지 않았다(2026-11-26). 워크플로 파일을 고쳐 다시 올리면 예약이 다시 등록된다.

- 잡초류 꽃가루 오퍼레이션 이름은 기상청 API 자체가 `getWeedsPollenRiskndxV3`(오타)다. 고치면 "없는 서비스" 오류가 난다.
- 꽃가루 API는 과거 자료를 주지 않는다("최근 1일 간의 자료만 제공"). 이력은 앱이 기기에 쌓는다.
- 에어코리아 측정소정보의 `dmX`는 위도, `dmY`는 경도다.
- 출처 표시: 기상청, 한국환경공단(에어코리아) — 공공누리 제1유형. 에어코리아 실시간 자료는 미확정 자료다.
