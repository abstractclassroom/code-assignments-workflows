import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile
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
        self.write(runtime.INSTRUCTOR, "name: Instructor tests\n")
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
                "instructorWorkflowDigest": runtime.sha(runtime.file_at(self.repo, self.source_commit, runtime.INSTRUCTOR)),
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
        for name in (runtime.WORKFLOW, runtime.INSTRUCTOR, runtime.CONFIG):
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
        self.assertEqual(self.snapshot["schemaVersion"], 3)
        self.assertEqual({entry["path"] for entry in self.snapshot["files"]},
                         {runtime.WORKFLOW, runtime.INSTRUCTOR, runtime.CONFIG, "tests/check", "build-config"})
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

    def test_score_accepts_zero_decimal_and_points_without_percentage_scale(self):
        for raw, expected in [("0", 0), ("82.5", 82.5), ("250", 250)]:
            self.assertEqual(runtime.grade_value(raw), expected)
        for raw in ["", "null", "true", "\"100\"", "[]", "-1", "NaN", "Infinity", "1e999", "9007199254740992", "score=100"]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                runtime.grade_value(raw)

    def test_receipt_displays_numeric_grade_and_matching_token_without_token_files(self):
        summary_file = self.root / "summary.md"
        response = {"token": "fixture.receipt.signature", "claims": {"grade": 82.5}}
        before = {p.relative_to(self.root) for p in self.root.rglob("*") if p.is_file()}
        with patch.dict(os.environ, {"SUBMISSION_PATH": str(self.repo), "ASSIGNMENT_ID": "aca_" + "x" * 32,
             "ASSIGNMENT_GRADE": "82.5", "GITHUB_STEP_SUMMARY": str(summary_file),
             "GITHUB_REPOSITORY": "student/assignment", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}), \
             patch.object(runtime, "oidc", return_value=("fixture", {"workflow_sha": self.source_commit})), \
             patch.object(runtime, "request", return_value=response) as request:
            runtime.receipt()
        self.assertEqual(request.call_args.args[2]["grade"], 82.5)
        self.assertEqual(request.call_args.args[2]["workflowDigest"], runtime.sha(runtime.file_at(self.repo, self.source_commit, runtime.WORKFLOW)))
        content = summary_file.read_text()
        self.assertIn("## Score: 82.5", content)
        self.assertIn("/actions/runs/123/attempts/2", content)
        self.assertIn("### Grade Token", content)
        self.assertTrue(content.endswith("```text\nfixture.receipt.signature\n```\n"))
        after = {p.relative_to(self.root) for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after - before, {Path("summary.md")})
        self.assertFalse(list(self.root.rglob("*.ast")))

    def test_prepared_workspace_contains_student_code_and_restored_files_only(self):
        self.write("tests/check", "student bypass")
        self.write("tests/extra", "remove me")
        self.write("student code.txt", "student implementation")
        self.write(".mvn/config", "hidden build settings")
        self.write("café/example.txt", "unicode file")
        self.write("run-tests.sh", "#!/bin/sh\nexit 0\n")
        (self.repo / "run-tests.sh").chmod(0o755)
        self.git("config", "core.filemode", "true")
        commit = self.commit()
        # Neither untracked runner files nor locally changed unprotected content
        # are allowed to enter the archive for the committed student submission.
        self.write("runner-credential.txt", "not for the artifact")
        self.write("student code.txt", "uncommitted change")
        archive = self.root / "workspace.tar.gz"
        runtime.prepare_workspace(self.repo, commit, self.package(commit), archive)
        with tarfile.open(archive) as package:
            names = package.getnames()
            self.assertTrue(all(member.isfile() for member in package.getmembers()))
            self.assertFalse(any(".git" in name.split("/") for name in names))
            self.assertNotIn("runner-credential.txt", names)
            self.assertNotIn("tests/extra", names)
            self.assertIn(".mvn/config", names)
            self.assertIn("café/example.txt", names)
            self.assertEqual(package.extractfile("student code.txt").read(), b"student implementation")
            self.assertEqual(package.extractfile("tests/check").read(), b"teacher assertions\n")
            self.assertEqual(package.getmember("run-tests.sh").mode, 0o755)
        extracted = self.root / "extracted"
        extracted.mkdir()
        subprocess.run(["tar", "-xzf", str(archive), "-C", str(extracted)], check=True)
        self.assertTrue(os.access(extracted / "run-tests.sh", os.X_OK))
        self.assertEqual((extracted / "tests/check").read_text(), "teacher assertions\n")

    def test_unlisted_instructor_solution_is_not_in_source_or_student_workspace(self):
        self.write("private-solution.java", "instructor-only solution")
        instructor_commit = self.commit()
        self.snapshot = runtime.source_package(self.repo, instructor_commit, "tests\nbuild-config")
        self.assertNotIn("private-solution.java", {entry["path"] for entry in self.snapshot["files"]})
        (self.repo / "private-solution.java").unlink()
        student_commit = self.commit()
        archive = self.root / "workspace.tar.gz"
        runtime.prepare_workspace(self.repo, student_commit, self.package(student_commit), archive)
        with tarfile.open(archive) as package:
            self.assertNotIn("private-solution.java", package.getnames())

    def test_protected_symlink_root_is_replaced_before_packaging(self):
        (self.repo / "tests/check").unlink()
        (self.repo / "tests").rmdir()
        (self.repo / "tests").symlink_to(self.root, target_is_directory=True)
        commit = self.commit()
        archive = self.root / "workspace.tar.gz"
        runtime.prepare_workspace(self.repo, commit, self.package(commit), archive)
        with tarfile.open(archive) as package:
            self.assertEqual(package.extractfile("tests/check").read(), b"teacher assertions\n")
            self.assertNotIn("tests", package.getnames())

    def test_unprotected_symlinks_and_submodules_are_rejected(self):
        (self.repo / "escape").symlink_to(self.root)
        commit = self.commit()
        with self.assertRaises(ValueError):
            runtime.prepare_workspace(self.repo, commit, self.package(commit), self.root / "symlink.tar.gz")
        (self.repo / "escape").unlink()
        commit = self.commit()
        self.git("update-index", "--add", "--cacheinfo", f"160000,{commit},submodule")
        self.git("-c", "user.name=Runtime tests", "-c", "user.email=runtime@example.invalid",
                 "commit", "-m", "submodule fixture")
        commit = self.git("rev-parse", "HEAD")
        with self.assertRaises(ValueError):
            runtime.prepare_workspace(self.repo, commit, self.package(commit), self.root / "submodule.tar.gz")

    def test_archive_paths_cannot_escape_or_carry_git_metadata(self):
        for name in ["", "/absolute", "../escape", "a/../escape", "a//b", "a/./b", ".git/config",
                     "nested/.git/config", "a\\b", "bad\nfile", "bad\x00file", "bad\x7ffile"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                runtime.workspace_path(name)
        self.assertEqual(runtime.workspace_path("folder with spaces/café.txt"), "folder with spaces/café.txt")

    def test_workspace_limits_remove_partial_archives(self):
        for limit in ["MAX_WORKSPACE_BYTES", "MAX_WORKSPACE_FILES"]:
            archive = self.root / f"{limit}.tar.gz"
            with patch.object(runtime, limit, 1), self.assertRaises(ValueError):
                runtime.prepare_workspace(self.repo, self.source_commit, self.package(self.source_commit), archive)
            self.assertFalse(archive.exists())

    def test_tampered_workflow_never_produces_a_workspace(self):
        self.write(runtime.INSTRUCTOR, "student modified grading")
        commit = self.commit()
        archive = self.root / "workspace.tar.gz"
        with self.assertRaises(ValueError):
            runtime.prepare_workspace(self.repo, commit, self.package(commit), archive)
        self.assertFalse(archive.exists())

    def test_prepare_grading_restores_and_exports_workspace_in_the_preparation_job(self):
        self.write("tests/check", "student bypass")
        self.write("tests/extra", "remove me")
        commit = self.commit()
        result = {**self.package(commit), "mode": "grade"}
        output = self.root / "outputs"
        package = self.root / "package"
        claims = {"workflow_sha": commit, "sha": commit}
        with patch.dict(os.environ, {"SUBMISSION_PATH": str(self.repo), "GITHUB_SHA": commit,
             "GITHUB_OUTPUT": str(output), "PACKAGE_PATH": str(package)}), \
             patch.object(runtime, "oidc", return_value=("fixture", claims)), \
             patch.object(runtime, "request", return_value=result):
            runtime.prepare()
        self.assertEqual([file.name for file in package.iterdir()], ["workspace.tar.gz"])
        self.assertIn("mode=grade", output.read_text())
        with tarfile.open(package / "workspace.tar.gz") as archive:
            self.assertEqual(archive.extractfile("tests/check").read(), b"teacher assertions\n")
            self.assertNotIn("tests/extra", archive.getnames())

    def test_preparation_failure_does_not_export_grade_mode_or_artifact(self):
        result = {**self.package(self.source_commit), "mode": "grade", "policyDigest": "0" * 64}
        output = self.root / "outputs"
        package = self.root / "package"
        claims = {"workflow_sha": self.source_commit, "sha": self.source_commit}
        with patch.dict(os.environ, {"SUBMISSION_PATH": str(self.repo), "GITHUB_SHA": self.source_commit,
             "GITHUB_OUTPUT": str(output), "PACKAGE_PATH": str(package)}), \
             patch.object(runtime, "oidc", return_value=("fixture", claims)), \
             patch.object(runtime, "request", return_value=result), self.assertRaises(ValueError):
            runtime.prepare()
        self.assertFalse(output.exists())
        self.assertFalse((package / "workspace.tar.gz").exists())

    def test_mismatched_score_is_not_displayed(self):
        summary_file = self.root / "summary.md"
        with patch.dict(os.environ, {"SUBMISSION_PATH": str(self.repo), "ASSIGNMENT_ID": "aca_" + "x" * 32,
             "ASSIGNMENT_GRADE": "10", "GITHUB_STEP_SUMMARY": str(summary_file)}), \
             patch.object(runtime, "oidc", return_value=("fixture", {"workflow_sha": self.source_commit})), \
             patch.object(runtime, "request", return_value={"token": "fixture.receipt.signature", "claims": {"grade": 100}}):
            with self.assertRaises(ValueError):
                runtime.receipt()
        self.assertFalse(summary_file.exists())


if __name__ == "__main__":
    unittest.main()
