import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("runtime", Path(__file__).resolve().parents[1] / "scripts/runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "submission"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.write(runtime.WORKFLOW, "name: Teacher workflow\n")
        self.write(runtime.CONFIG, json.dumps({"assignmentId": "aca_" + "x" * 32, "files": ["tests", "build-config"], "sourceVersion": "main"}))
        self.write("tests/check", "teacher assertions\n")
        self.write("build-config", "teacher build\n")
        self.write("student code.txt", "original code\n")
        self.source_commit = self.commit()
        self.snapshot = runtime.source_package(self.repo, self.source_commit, "tests\nbuild-config")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def write(self, name, data):
        file = self.repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(data)

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Runtime tests", "-c", "user.email=runtime@example.invalid", "commit", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def package(self, commit):
        return {"snapshot": self.snapshot, "policyDigest": runtime.sha(runtime.canonical(self.snapshot)),
                "workflowDigest": runtime.sha(runtime.file_at(self.repo, self.source_commit, runtime.WORKFLOW)),
                "configDigest": runtime.sha(runtime.file_at(self.repo, self.source_commit, runtime.CONFIG)), "commitSha": commit}

    def test_exact_deletion_and_restoration_preserve_student_files(self):
        self.write("tests/check", "student changed assertions")
        self.write("tests/extra", "student addition must be deleted")
        self.write("build-config", "student changed build")
        self.write("student code.txt", "student implementation")
        commit = self.commit()
        runtime.restore_files(self.repo, commit, self.package(commit))
        self.assertEqual((self.repo / "tests/check").read_text(), "teacher assertions\n")
        self.assertFalse((self.repo / "tests/extra").exists())
        self.assertEqual((self.repo / "build-config").read_text(), "teacher build\n")
        self.assertEqual((self.repo / "student code.txt").read_text(), "student implementation")

    def test_workflow_and_json_tampering_fail_before_replacement(self):
        for name in (runtime.WORKFLOW, runtime.CONFIG):
            with self.subTest(name=name):
                original = (self.repo / name).read_bytes()
                self.write(name, "changed")
                self.write("tests/check", "student file")
                commit = self.commit()
                with self.assertRaises(ValueError):
                    runtime.restore_files(self.repo, commit, self.package(commit))
                self.assertEqual((self.repo / "tests/check").read_text(), "student file")
                (self.repo / name).write_bytes(original)
                self.commit()

    def test_symlink_root_is_deleted_without_touching_its_target(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "sentinel").write_text("keep")
        (self.repo / "tests/check").unlink()
        (self.repo / "tests").rmdir()
        (self.repo / "tests").symlink_to(outside, target_is_directory=True)
        commit = self.commit()
        runtime.restore_files(self.repo, commit, self.package(commit))
        self.assertFalse((self.repo / "tests").is_symlink())
        self.assertEqual((outside / "sentinel").read_text(), "keep")

    def test_symlink_parent_and_changed_package_are_rejected(self):
        self.snapshot["replacePaths"] = ["escape/tests"]
        (self.repo / "escape").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            runtime.restore_files(self.repo, self.source_commit, self.package(self.source_commit))
        source = self.package(self.source_commit)
        source["policyDigest"] = "0" * 64
        with self.assertRaises(ValueError):
            runtime.restore_files(self.repo, self.source_commit, source)

    def test_source_contains_only_json_workflow_and_listed_paths(self):
        self.assertEqual(self.snapshot["schemaVersion"], 2)
        self.assertEqual({entry["path"] for entry in self.snapshot["files"]},
                         {runtime.WORKFLOW, runtime.CONFIG, "tests/check", "build-config"})
        with self.assertRaises(ValueError):
            runtime.safe_path("../outside")

    def test_prepare_reads_three_field_json_and_handles_unpublished_version(self):
        output = self.root / "outputs"
        claims = {"workflow_sha": self.source_commit, "sha": self.source_commit}
        with patch.dict(os.environ, {"SUBMISSION_PATH": str(self.repo), "GITHUB_SHA": self.source_commit, "GITHUB_OUTPUT": str(output)}), \
             patch.object(runtime, "oidc", return_value=("fixture", claims)), \
             patch.object(runtime, "request", return_value={"mode": "source_waiting"}) as request:
            runtime.prepare()
        body = request.call_args.args[2]
        self.assertEqual(body["workflowDigest"], runtime.sha(runtime.file_at(self.repo, self.source_commit, runtime.WORKFLOW)))
        self.assertEqual(set(json.loads(base64.b64decode(body["configuration"]))), {"assignmentId", "files", "sourceVersion"})
        self.assertIn("mode=source_waiting", output.read_text())


if __name__ == "__main__":
    unittest.main()
