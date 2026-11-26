"""꽃가루는 11~2월에 API가 데이터를 주지 않아서, 시즌 중 동작은 가짜 응답으로 검사한다."""
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("DATA_GO_KR_KEY", "test")
import build  # noqa: E402

KST = build.KST


def fake_pollen_response(today, tomorrow, d2, d3):
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                         "body": {"items": {"item": [{"code": "D07", "areaNo": "1111000000", "date": "2026040106",
                                                      "today": today, "tomorrow": tomorrow,
                                                      "dayaftertomorrow": d2, "todaysaftertomorrow": d3}]}}}}


class IssueTime(unittest.TestCase):
    def test_before_morning_issue_uses_yesterday_evening(self):
        self.assertEqual(build.pollen_expected_issue(datetime(2026, 4, 2, 6, 30, tzinfo=KST)), "2026040118")

    def test_after_morning_issue(self):
        self.assertEqual(build.pollen_expected_issue(datetime(2026, 4, 2, 6, 45, tzinfo=KST)), "2026040206")

    def test_after_evening_issue(self):
        self.assertEqual(build.pollen_expected_issue(datetime(2026, 4, 2, 19, 0, tzinfo=KST)), "2026040218")


class Pollen(unittest.TestCase):
    def setUp(self):
        self.orig = build.call
        build.errors.clear()

    def tearDown(self):
        build.call = self.orig

    def test_off_season_makes_no_calls(self):
        build.call = lambda *a, **k: self.fail("should not call API off-season")
        p = build.build_pollen(datetime(2026, 11, 26, 12, tzinfo=KST), None)
        self.assertEqual(p["season"], {"oak": False, "pine": False, "weeds": False})
        self.assertEqual(p["areas"], {})

    def test_spring_fetches_oak_and_pine_only(self):
        ops = []

        def fake(url, params, **k):
            ops.append(url.rsplit("/", 1)[-1])
            return fake_pollen_response("2", "3", "1", "")  # 18시 발표 등으로 빈 값이 올 수 있음
        build.call = fake
        p = build.build_pollen(datetime(2026, 4, 2, 7, tzinfo=KST), None)
        self.assertEqual(p["issued"], "2026040206")
        self.assertEqual(set(ops), {"getOakPollenRiskIdxV3", "getPinePollenRiskIdxV3"})
        self.assertEqual(len(p["areas"]), len(build.REGIONS))
        self.assertEqual(p["areas"]["1111000000"]["oak"], [2, 3, 1, None])
        self.assertEqual(build.errors, [])

    def test_autumn_uses_weeds_operation_with_typo_name(self):
        ops = set()

        def fake(url, params, **k):
            ops.add(url.rsplit("/", 1)[-1])
            return fake_pollen_response("1", "1", "0", "0")
        build.call = fake
        build.build_pollen(datetime(2026, 9, 10, 8, tzinfo=KST), None)
        self.assertEqual(ops, {"getWeedsPollenRiskndxV3"})

    def test_failed_area_keeps_previous_value(self):
        prev = {"pollen": {"season": {"oak": True, "pine": True, "weeds": False}, "issued": "2026040118",
                           "areas": {"1111000000": {"oak": [1, 1, 1, 1], "pine": [0, 0, 0, 0]}}}}

        def fake(url, params, **k):
            if params["areaNo"] == "1111000000":
                raise RuntimeError("timeout")
            return fake_pollen_response("3", "3", "3", "3")
        build.call = fake
        p = build.build_pollen(datetime(2026, 4, 2, 7, tzinfo=KST), prev)
        self.assertEqual(p["areas"]["1111000000"]["oak"], [1, 1, 1, 1])
        self.assertEqual(p["areas"]["1114000000"]["oak"], [3, 3, 3, 3])
        self.assertTrue(build.errors)

    def test_same_issue_is_not_refetched(self):
        prev = {"pollen": {"season": {"oak": True, "pine": True, "weeds": False}, "issued": "2026040206",
                           "areas": {"1111000000": {"oak": [1, 1, 1, 1]}}}}
        build.call = lambda *a, **k: self.fail("should reuse previous issue")
        p = build.build_pollen(datetime(2026, 4, 2, 9, tzinfo=KST), prev)
        self.assertIs(p, prev["pollen"])


class Geo(unittest.TestCase):
    def test_km(self):
        # 서울시청 ~ 부산시청 약 325km
        self.assertAlmostEqual(build.km(37.5663, 126.9779, 35.1798, 129.0750), 325, delta=10)


if __name__ == "__main__":
    unittest.main()
