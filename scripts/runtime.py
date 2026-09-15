"""Trusted GitHub runner control code. Never import files from a submission."""
import base64
import hashlib
import json
import math
import os
import re
import subprocess
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://preview.abstractclassroom.com/api/code-assignments/github/workflow"
AUDIENCE = "abstractclassroom-code-assignments"
WORKFLOW = ".github/workflows/auto-grading-workflow.yml"
CONFIG = ".github/workflows/auto-grading-config.json"
INSTRUCTOR = ".github/workflows/instructor_autograder.yml"
MAX_BYTES = 2 * 1024 * 1024


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def safe_path(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.\-/]{1,240}", value):
        raise ValueError("Invalid repository path")
    if any(part in ("", ".", "..", ".git") for part in value.split("/")):
        raise ValueError("Repository path escapes its workspace")
    return value


def request(url, headers, body=None):
    req = urllib.request.Request(url, data=canonical(body) if body is not None else None,
                                 headers={**headers, "Content-Type": "application/json"})
    # Tokens must never follow redirects to another origin.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=45) as response:
            data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError("Response too large")
            return json.loads(data)
    except urllib.error.HTTPError as error:
        # Do not print response bodies: source pairing secrets and JWTs stay out of logs.
        code = "request_failed"
        try:
            supplied = json.loads(error.read(4096)).get("error", {}).get("code", "")
            if re.fullmatch(r"[a-z_]{1,80}", supplied):
                code = supplied
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f"AbstractClassroom request failed (HTTP {error.code}, {code}); see the shared workflow setup guide.") from None


def oidc():
    raw = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    url = urllib.parse.urlsplit(raw)
    if url.scheme != "https" or not url.hostname or not url.hostname.endswith(".actions.githubusercontent.com"):
        raise ValueError("Unexpected GitHub OIDC endpoint")
    query = urllib.parse.parse_qsl(url.query)
    query.append(("audience", AUDIENCE))
    response = request(urllib.parse.urlunsplit(url._replace(query=urllib.parse.urlencode(query))),
                       {"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
    token = response["value"]
    # Local claims select the exact files; the API independently verifies GitHub's signature.
    payload = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return token, claims


def call(body):
    token, claims = oidc()
    return request(API, {"Authorization": "Bearer " + token}, body), claims


def git(repo, *arguments):
    return subprocess.check_output(["git", "-C", str(repo), *arguments], stderr=subprocess.DEVNULL)


def tree(repo, commit):
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Invalid commit SHA")
    result = []
    for row in git(repo, "ls-tree", "-rz", commit).split(b"\0"):
        if not row:
            continue
        meta, raw_path = row.split(b"\t", 1)
        mode, kind, blob = meta.decode().split(" ")
        path = raw_path.decode()
        result.append((path, mode, kind, blob))
    return result


def blob(repo, oid):
    if not re.fullmatch(r"[a-f0-9]{40}", oid):
        raise ValueError("Invalid Git object")
    size = int(git(repo, "cat-file", "-s", oid))
    if size > 10 * 1024 * 1024:
        raise ValueError("Individual submission files must be at most 10 MiB")
    return git(repo, "cat-file", "blob", oid)


def file_at(repo, commit, path):
    entries = [entry for entry in tree(repo, commit) if entry[0] == path]
    if len(entries) != 1 or entries[0][1] not in ("100644", "100755") or entries[0][2] != "blob":
        raise ValueError("Required source file is missing or is not a regular file")
    return blob(repo, entries[0][3])


def source_package(repo, commit, replace_paths):
    roots = sorted(safe_path(path.strip()) for path in replace_paths.splitlines() if path.strip())
    files = []
    total = 0
    for path, mode, kind, oid in tree(repo, commit):
        if path not in (WORKFLOW, INSTRUCTOR, CONFIG) and not any(path == root or path.startswith(root + "/") for root in roots):
            continue
        if mode not in ("100644", "100755") or kind != "blob":
            raise ValueError("Protected source paths cannot contain symlinks or submodules")
        content = blob(repo, oid)
        total += len(content)
        if total > MAX_BYTES or len(files) >= 500:
            raise ValueError("Protected files exceed the 2 MiB / 500 file limit")
        files.append({"path": path, "mode": mode, "content": base64.b64encode(content).decode()})
    return {"schemaVersion": 3, "replacePaths": roots, "files": sorted(files, key=lambda item: item["path"])}


def output(name, value):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", str(value)):
        raise ValueError("Invalid workflow output")
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(f"{name}={value}\n")


def prepare():
    repo = Path(os.environ["SUBMISSION_PATH"])
    token, claims = oidc()
    commit = claims["workflow_sha"]
    configuration = file_at(repo, commit, CONFIG)
    config = json.loads(configuration)
    assignment = config.get("assignmentId", "")
    if not re.fullmatch(r"aca_[A-Za-z0-9_-]{32}", assignment):
        raise ValueError("Set assignmentId in auto-grading-config.json to the public ID from your dashboard")
    if commit != os.environ["GITHUB_SHA"] or claims["sha"] != commit:
        raise ValueError("The checkout and executed workflow revision do not match")
    workflow = file_at(repo, commit, WORKFLOW)
    result = request(API, {"Authorization": "Bearer " + token}, {"action": "prepare", "assignmentId": assignment,
        "workflow": base64.b64encode(workflow).decode(), "workflowDigest": sha(workflow),
        "instructorWorkflow": base64.b64encode(file_at(repo, commit, INSTRUCTOR)).decode(), "configuration": base64.b64encode(configuration).decode(), "pairingToken": os.environ.get("SOURCE_PAIRING_TOKEN", "")})
    if result["mode"] == "source":
        snapshot = source_package(repo, commit, "\n".join(config["files"]))
        call({"action": "publish", "assignmentId": assignment, "snapshot": snapshot})
        print("Instructor source published. Students can now submit this assignment.")
    elif result["mode"] == "source_waiting":
        print("Source publication waits for a push or workflow run on the branch/tag named by sourceVersion.")
    elif result["mode"] == "grade":
        if result["instructorWorkflowDigest"] != sha(file_at(repo, commit, INSTRUCTOR)) or result["workflowDigest"] != sha(workflow) or result["configDigest"] != sha(configuration) or result["commitSha"] != commit:
            raise ValueError("The source workflow or submission revision does not match")
        package = Path(os.environ["PACKAGE_PATH"])
        package.mkdir(parents=True, exist_ok=False)
        (package / "source.json").write_bytes(canonical(result))
    else:
        raise ValueError("Unexpected assignment mode")
    output("mode", result["mode"])
    output("assignment-id", assignment)


def grade_value(raw):
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("The final score must be a nonnegative number") from None
    if type(value) not in (int, float) or not 0 <= value <= 9007199254740991 or not math.isfinite(value):
        raise ValueError("The final score must be a finite nonnegative number within the supported numeric range")
    return value


def receipt():
    grade = grade_value(os.environ.get("ASSIGNMENT_GRADE", ""))
    token, claims = oidc()
    repo = Path(os.environ["SUBMISSION_PATH"])
    workflow = file_at(repo, claims["workflow_sha"], WORKFLOW)
    result = request(API, {"Authorization": "Bearer " + token}, {
        "action": "receipt", "assignmentId": os.environ["ASSIGNMENT_ID"], "grade": grade,
        "workflow": base64.b64encode(workflow).decode(), "workflowDigest": sha(workflow),
        "instructorWorkflow": base64.b64encode(file_at(repo, claims["workflow_sha"], INSTRUCTOR)).decode(),
        "configuration": base64.b64encode(file_at(Path(os.environ["SUBMISSION_PATH"]), claims["workflow_sha"], CONFIG)).decode()})
    token = result.get("token", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token) or result.get("claims", {}).get("grade") != grade:
        raise ValueError("Invalid grade token response")
    summary(grade, token)


def summary(grade, token):
    repo, run, attempt = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not run.isdigit() or not attempt.isdigit():
        raise ValueError("Invalid workflow run identity")
    url = f"https://github.com/{repo}/actions/runs/{run}/attempts/{attempt}"
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
        stream.write(f"## Score: {grade}\n\nRun [{run}, attempt {attempt}]({url})\n\n")
        stream.write("### Grade token\n\nCopy the token below and paste it into your LMS submission.\n\n")
        stream.write(f"```text\n{token}\n```\n")
    print("Score and grade token are available in the workflow run summary.")


def restore_files(repo, commit, source):
    """Restore only listed paths; never overwrite the workflow being verified."""
    repo = repo.resolve()
    snapshot = source["snapshot"]
    if snapshot.get("schemaVersion") != 3 or sha(canonical(snapshot)) != source["policyDigest"]:
        raise ValueError("Registered source package integrity check failed")
    instructor = file_at(repo, commit, INSTRUCTOR)
    instructor_path = repo / INSTRUCTOR
    if (sha(instructor) != source["instructorWorkflowDigest"] or instructor_path.is_symlink()
            or not instructor_path.parent.resolve().is_relative_to(repo) or instructor_path.read_bytes() != instructor):
        raise ValueError("instructor_autograder.yml does not match the registered instructor source")
    original = file_at(repo, commit, WORKFLOW)
    current = repo / WORKFLOW
    if (source["commitSha"] != commit or sha(original) != source["workflowDigest"]
            or current.is_symlink() or not current.parent.resolve().is_relative_to(repo)
            or current.read_bytes() != original):
        raise ValueError("auto-grading-workflow.yml was changed; restore the instructor's original file")
    configuration = file_at(repo, commit, CONFIG)
    if sha(configuration) != source["configDigest"] or (repo / CONFIG).is_symlink() or (repo / CONFIG).read_bytes() != configuration:
        raise ValueError("auto-grading-config.json was changed; restore the instructor's original file")
    roots = [safe_path(value) for value in snapshot["replacePaths"]]
    for index, root in enumerate(roots):
        if root == ".github" or root.startswith(".github/") or any(
                root == other or root.startswith(other + "/") or other.startswith(root + "/")
                for other in roots[:index]):
            raise ValueError("Invalid or overlapping replacement paths")
        target = repo / root
        if not target.parent.resolve().is_relative_to(repo):
            raise ValueError("Replacement path crosses a symlink outside the checkout")
        # Reject symlink ancestors even when they happen to point inside the checkout.
        parent = target.parent
        while parent != repo:
            if parent.is_symlink():
                raise ValueError("Replacement path has a symlink ancestor")
            parent = parent.parent
    for root in roots:
        target = repo / root
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
    for entry in snapshot["files"]:
        name = safe_path(entry["path"])
        if name in (WORKFLOW, INSTRUCTOR, CONFIG):
            if base64.b64decode(entry["content"], validate=True) != {WORKFLOW: original, INSTRUCTOR: instructor, CONFIG: configuration}[name]:
                raise ValueError("Registered workflow does not match")
            continue
        if entry["mode"] not in ("100644", "100755") or not any(
                name == root or name.startswith(root + "/") for root in roots):
            raise ValueError("Unexpected file in registered source package")
        target = repo / name
        if not target.parent.resolve().is_relative_to(repo):
            raise ValueError("Source file escapes the checkout")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(entry["content"], validate=True))
        target.chmod(0o755 if entry["mode"] == "100755" else 0o644)


def restore():
    package = Path(os.environ["SOURCE_PACKAGE"])
    if package.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Source package is too large")
    source = json.loads(package.read_bytes())
    restore_files(Path(os.environ["GITHUB_WORKSPACE"]), os.environ["GITHUB_SHA"], source)
    print("Workflow verified. Listed instructor paths were deleted and restored before grading.")

if __name__ == "__main__":
    import sys
    try:
        {"prepare": prepare, "receipt": receipt, "restore": restore}[sys.argv[1]]()
    except Exception as error:
        # Do not include variable data or command output in workflow annotations.
        print("AbstractClassroom stopped: " + json.dumps(str(error)))
        sys.exit(1)
