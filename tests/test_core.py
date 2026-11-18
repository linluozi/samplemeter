import json
import os
import random
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from samplemeter import Auditor, fingerprint, scan_seed_resets  # noqa: E402


def branchy(value, mode=0):
    if mode == 0:
        if value is None:
            return "none"
        if isinstance(value, int):
            return value * 2
        return str(value)
    if mode == 1:
        for i in range(3):
            value = (value, i)
        return value
    return value


class FingerprintTests(unittest.TestCase):
    def test_types_are_not_conflated(self):
        values = [1, 1.0, True, "1", b"1", None]
        self.assertEqual(len({fingerprint(v) for v in values}), len(values))

    def test_dict_order_does_not_create_diversity(self):
        self.assertEqual(fingerprint({"a": 1, "b": [2, 3]}), fingerprint({"b": [2, 3], "a": 1}))

    def test_set_order_does_not_create_diversity(self):
        self.assertEqual(fingerprint({"x", "y", "z"}), fingerprint({"z", "y", "x"}))

    def test_nan_is_stable(self):
        self.assertEqual(fingerprint(float("nan")), fingerprint(float("nan")))

    def test_negative_zero_differs_from_zero(self):
        self.assertNotEqual(fingerprint(-0.0), fingerprint(0.0))

    def test_non_ascii_is_preserved(self):
        self.assertEqual(fingerprint("算法"), fingerprint("算法"))
        self.assertNotEqual(fingerprint("算法"), fingerprint("算法 "))

    def test_opaque_objects_are_marked(self):
        class Opaque:
            __slots__ = ()

        self.assertTrue(fingerprint(Opaque()).startswith("opaque:"))

    def test_deep_nesting_is_bounded(self):
        payload = "x"
        for _ in range(40):
            payload = [payload]
        self.assertIn("depth-limit", fingerprint(payload))


class AuditorTests(unittest.TestCase):
    def test_counts_unique_inputs_not_iterations(self):
        auditor = Auditor()
        wrapped = auditor.wrap(branchy)
        for _ in range(10):
            wrapped(1)
        wrapped(2)
        report = auditor.report()
        self.assertEqual(report.nominal, 11)
        self.assertEqual(report.unique_inputs, 2)
        self.assertEqual(report.inflation, 5.5)

    def test_kwargs_are_part_of_the_input(self):
        auditor = Auditor()
        wrapped = auditor.wrap(branchy)
        wrapped(1)
        wrapped(value=1)
        self.assertEqual(auditor.report().unique_inputs, 2)

    def test_paths_are_distinguished(self):
        auditor = Auditor()
        wrapped = auditor.wrap(branchy)
        wrapped(None)
        wrapped(7)
        wrapped("s")
        report = auditor.report()
        self.assertEqual(report.unique_inputs, 3)
        self.assertGreaterEqual(report.unique_paths, 2)

    def test_wrapper_is_transparent(self):
        auditor = Auditor()
        wrapped = auditor.wrap(branchy)
        self.assertEqual(wrapped(21), 42)
        self.assertEqual(wrapped.__name__, "branchy")

    def test_exceptions_propagate_and_are_counted(self):
        auditor = Auditor()

        def boom(value):
            raise ValueError(value)

        wrapped = auditor.wrap(boom)
        with self.assertRaises(ValueError):
            wrapped(1)
        with self.assertRaises(ValueError):
            wrapped(1)
        report = auditor.report()
        self.assertEqual(report.nominal, 2)
        self.assertEqual(report.unique_inputs, 1)
        self.assertEqual(report.raised, 2)

    def test_scope_validation(self):
        with self.assertRaises(ValueError):
            Auditor(scope="everything")

    def test_methods_skip_self_by_default(self):
        class Box:
            def get(self, key):
                return key

        auditor = Auditor()
        box = Box()
        box.get = auditor.wrap(Box.get).__get__(box, Box)
        box.get("a")
        box.get("a")
        report = auditor.report()
        self.assertEqual(report.nominal, 2)
        self.assertEqual(report.unique_inputs, 1)


class SeedProbeTests(unittest.TestCase):
    def test_repeated_seed_site_is_reported(self):
        def seed_each_call(value):
            random.seed(4321)
            return value

        original = random.seed
        auditor = Auditor()
        wrapped = auditor.wrap(seed_each_call)
        auditor._install_seed_probe()
        try:
            for _ in range(3):
                wrapped(1)
        finally:
            auditor._remove_seed_probe()

        self.assertIs(random.seed, original)
        report = auditor.report()
        self.assertEqual(len(report.repeated_seed_sites()), 1)
        self.assertEqual(report.repeated_seed_sites()[0].calls, 3)
        self.assertTrue(report.is_degenerate())

    def test_probe_is_restored_on_exception(self):
        original = random.seed
        auditor = Auditor()
        auditor._install_seed_probe()
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            pass
        finally:
            auditor._remove_seed_probe()
        self.assertIs(random.seed, original)


class StaticScanTests(unittest.TestCase):
    def _write(self, source):
        handle = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
        handle.write(textwrap.dedent(source))
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_seed_inside_loop_is_flagged(self):
        path = self._write(
            """
            import random

            for i in range(3):
                random.seed(i)
            """
        )
        findings = scan_seed_resets(path)
        self.assertEqual(len(findings), 1)
        self.assertIn("inside a loop body", findings[0])

    def test_seed_outside_loop_is_not_flagged(self):
        path = self._write(
            """
            import random

            random.seed(1)
            for i in range(3):
                print(i)
            """
        )
        self.assertEqual(scan_seed_resets(path), [])

    def test_syntax_error_is_reported_not_raised(self):
        path = self._write("def broken(:\n")
        findings = scan_seed_resets(path)
        self.assertEqual(len(findings), 1)
        self.assertIn("cannot parse", findings[0])


class CliTests(unittest.TestCase):
    def _run(self, harness, target, *extra):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(ROOT, "src")
        env["SM_N"] = "200"
        return subprocess.run(
            [sys.executable, "-m", "samplemeter", "run", harness, "--target", target, *extra],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_inflated_harness_is_caught(self):
        result = self._run(
            "demo/inflated_harness.py",
            "demo/inflated_lib.py:resolve",
            "--json",
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["nominal"], 200)
        self.assertEqual(payload["unique_inputs"], 1)
        self.assertEqual(payload["unique_paths"], 1)
        self.assertTrue(payload["degenerate"])
        self.assertEqual(payload["inflation"], 200.0)

    def test_honest_harness_passes(self):
        result = self._run(
            "demo/honest_harness.py",
            "demo/honest_lib.py:resolve",
            "--json",
            "--min-unique",
            "190",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["unique_inputs"], 200)
        self.assertFalse(payload["degenerate"])

    def test_path_collapse_is_caught_by_min_paths(self):
        result = self._run(
            "demo/collapsed_harness.py",
            "demo/collapsed_lib.py:validate",
            "--json",
            "--min-paths",
            "5",
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["unique_inputs"], 200)
        self.assertEqual(payload["unique_paths"], 1)
        self.assertTrue(payload["degenerate"])

    def test_real_corpus_harness_runs(self):
        result = self._run(
            "demo/real_corpus_harness.py",
            "demo/real_corpus_lib.py:parse_requirement",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertGreater(payload["nominal"], 0)
        self.assertGreaterEqual(payload["unique_inputs"], 1)


if __name__ == "__main__":
    unittest.main()
