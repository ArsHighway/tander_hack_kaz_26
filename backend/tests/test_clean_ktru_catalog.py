import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import clean_ktru_catalog as cleanup


class CleanupChecks(unittest.TestCase):
    now = dt.datetime.fromisoformat("2026-10-11T00:00:00+03:00")

    def record(self, **changes):
        return {
            "status": "ACTIVE",
            "actual_file": True,
            "actual": "",
            "cancelDate": "",
            "applicationDateEnd": "",
            "applicationDateStart": "",
            "isTemplate": "false",
            "noNewFeatures": "true",
            "okpd2": "25.73.30.141",
            "name": "Молоток",
            "code": "25.73.30.141-00000001",
            **changes,
        }

    def test_missing_ftp_actual_is_valid_for_non_template(self):
        self.assertIsNone(cleanup.exclusion(self.record(), self.now))

    def test_templates_are_excluded_and_non_templates_remain(self):
        for flag in ("true", "1"):
            self.assertEqual(cleanup.exclusion(self.record(isTemplate=flag), self.now), "template")
        for flag in ("false", "0"):
            self.assertIsNone(cleanup.exclusion(self.record(isTemplate=flag), self.now))

    def test_missing_template_flag_cannot_be_assumed_false(self):
        with self.assertRaises(ValueError):
            cleanup.exclusion(self.record(isTemplate=""), self.now)

    def test_inactive_and_terminated_are_excluded(self):
        for status in ("INACTIVE", "TERMINATED", ""):
            self.assertIsNotNone(cleanup.exclusion(self.record(status=status), self.now))

    def test_false_actual_in_both_xml_spellings(self):
        for actual in ("false", "0"):
            self.assertEqual(
                cleanup.exclusion(self.record(actual=actual), self.now), "position_not_actual"
            )

    def test_end_date_and_timezones(self):
        self.assertEqual(
            cleanup.exclusion(self.record(applicationDateEnd="2026-10-10T20:59:59Z"), self.now),
            "application_ended",
        )
        self.assertIsNone(
            cleanup.exclusion(self.record(applicationDateEnd="2026-10-10T21:00:01Z"), self.now)
        )
        self.assertIsNone(cleanup.exclusion(self.record(applicationDateEnd=""), self.now))

    def test_future_mandatory_start_does_not_mean_expired(self):
        self.assertIsNone(
            cleanup.exclusion(
                self.record(applicationDateStart="2027-01-01T00:00:00+03:00"), self.now
            )
        )

    def test_cancellation_timing(self):
        self.assertEqual(
            cleanup.exclusion(self.record(cancelDate="2026-10-10T00:00:00+03:00"), self.now),
            "cancelled",
        )
        self.assertIsNone(
            cleanup.exclusion(self.record(cancelDate="2027-01-01T00:00:00+03:00"), self.now)
        )

    def test_new_terminated_version_does_not_resurrect_old_active(self):
        code = "25.73.30.141-00000001"
        test_parent = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(dir=test_parent) as directory:
            root = Path(directory)
            self.assertTrue(root.resolve().is_relative_to(test_parent))
            body = (
                "<root>"
                + "".join(
                    f"<position><data><code>{code}</code><version>{version}</version><name>Молоток</name>"
                    f"<status>{status}</status><OKPD2><code>25.73.30.141</code></OKPD2></data></position>"
                    for version, status in ((1, "ACTIVE"), (2, "TERMINATED"))
                )
                + "</root>"
            )
            (root / "nsiKTRUNew_all_actual_test.xml").write_text(body, encoding="utf-8")
            records, _ = cleanup.scan_xml(root, {code})
            self.assertEqual(cleanup.exclusion(records[code], self.now), "status_TERMINATED")

    def test_medical_tools_and_services_are_outside_goods_scope(self):
        self.assertIsNone(cleanup.category_of("32.50.13.190"))
        self.assertTrue(cleanup.is_service("Услуги по производству мебели"))
        self.assertIsNone(cleanup.category_of("25.730.01.123"))

    def test_distinct_codes_same_name_and_optional_characteristic_are_preserved(self):
        codes = ["25.73.30.141-00000001", "25.73.30.141-00000002"]
        chars = [{"name": "Материал", "kind": "qualitative", "values": ["Сталь"]}]
        positions = [
            {
                "code": c,
                "name": "Молоток",
                "okpd2": "25.73.30.141",
                "source": "test",
                "characteristics": chars,
            }
            for c in codes
        ]
        positions += [
            {
                "code": "14.12.11.110-00000001",
                "name": "Куртка",
                "okpd2": "14.12.11.110",
                "source": "test",
                "characteristics": [],
            },
            {
                "code": "31.01.11.110-00000001",
                "name": "Стул",
                "okpd2": "31.01.11.110",
                "source": "test",
                "characteristics": [],
            },
        ]
        metadata = {
            p["code"]: self.record(
                name=p["name"],
                okpd2=p["okpd2"],
                characteristics=[{"name": "Материал", "actual": "true", "isRequired": "false"}]
                if p["characteristics"]
                else [],
            )
            for p in positions
        }
        output, _ = cleanup.clean({"positions": positions}, metadata, self.now)
        self.assertEqual(len(output["positions"]), 4)
        self.assertEqual(output["positions"][0]["characteristics"][0]["values"], ["Сталь"])


if __name__ == "__main__":
    unittest.main()
