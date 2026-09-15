# AbstractClassroom Code Assignments workflows

Shared preparation, instructor-action execution, and API Grade Token requests.
All jobs run in the caller's GitHub account. The API alone signs Grade Tokens.

## Assignment files

The [instructor starter](https://github.com/abstractclassroom/code-assignments-template)
has three verified files and one small manual-run wrapper:

```text
.github/actions/instructor/action.yml          # Instructor tests and scoring
.github/workflows/auto-grading-config.json       # Assignment and protected paths
.github/workflows/auto-grading-workflow.yml      # Automatic coordinator
.github/workflows/instructor_autograder.yml     # Manual score-only wrapper
```

Keep these paths. Only the instructor action contains grading logic. It has no
checkout, artifact handling, API calls or token-generation steps. The manual
wrapper calls the same shared grading workflow without contacting the API.

## Instructor setup

1. [Register a Code Assignments course](https://preview.abstractclassroom.com/code-assignments/register/)
   and create an assignment. The subscription is $5/course/month, with no free trial.
2. Create an instructor repository from the starter. Configure:

   ```json
   {
     "assignmentId": "YOUR_ASSIGNMENT_ID",
     "files": ["tests", "build-config"],
     "sourceVersion": "main"
   }
   ```

   Use the public assignment ID from the dashboard. Listed paths are **deleted
   completely and replaced**, not merged. Files outside those paths are preserved.
   The coordinator, instructor action and JSON are checked automatically. Do not
   list them in `files` or use paths under `.github`.
3. Add starter code and tests. Edit `.github/actions/instructor/action.yml`
   between **Initialize score** and **Save final score**. Add ordinary tool setup,
   tests and score calculation. Persist score updates through `$GITHUB_ENV`.
4. Store the one-time pairing token in the instructor repository's Actions secret
   `ABSTRACTCLASSROOM_SOURCE_TOKEN`. It expires after two hours and is consumed
   at first pairing. Never commit it or copy it to students.
5. Push the branch/tag named by `sourceVersion`, or dispatch **Auto grading**
   on that ref. The registered source publishes only the verified files and listed
   protected paths. Later runs use GitHub OIDC without a secret or GitHub App.
6. Enable **Template repository** and share it with students. Student copies must
   retain identical coordinator, action and JSON bytes for the selected source.

## Instructor action and scoring

The instructor action initializes lowercase `score` to zero and exposes the
final numeric score. Native steps can add points:

```yaml
    - name: Score the first check
      shell: bash
      run: |
        if ./check-one; then
          score=$((score + 10))
        fi
        printf 'score=%s\n' "$score" >> "$GITHUB_ENV"
```

Keep initialization first and **Save final score** last. The final step exports
`score` through `$GITHUB_OUTPUT`. The shared workflow calls the action and
passes that output to the coordinator and API. No separate grading-policy JSON,
test report format, build system, or container is required. The empty starter
reports zero until the instructor adds assessment steps.

Scores may be zero, fractional, or exceed 100. They must be finite, nonnegative,
and at most 9007199254740991. The API assumes no percentage scale or passing mark.
Expected test failures can earn partial credit if the action handles them and
finishes successfully. Unexpected failures, cancellation and timeouts prevent
token issuance. The shared grading timeout defaults to 10 minutes; instructors
may set `timeout-minutes` from 1 to 60 under the coordinator grading call's
`with` block before republishing.

For independent checks, use **Actions → Instructor autograder → Run workflow**.
The manual wrapper checks out the selected commit and invokes the same action.
It requires no API access, subscription, pairing or secret. It displays a score
but does not issue a Grade Token. Automatic pushes run only **Auto grading**.

## Execution and trust

1. The trusted preparation job checks the original coordinator, instructor action,
   and JSON against the selected published source. It deletes/replaces protected
   paths and packages the prepared student workspace. It executes no student code.
2. A separate grading job downloads/unpacks that workspace and calls the local
   instructor action on the same runner. It has read-only repository/Actions
   permissions, no OIDC write permission and no instructor pairing secret.
3. After successful grading, a separate trusted **Grade Token** job checks the
   original files again and requests the API-signed result. Signing keys remain
   on the server.

Referencing shared workflows does not move grading into AbstractClassroom's GitHub
account. Hosted jobs use separate machines; the prepared artifact passes files
between preparation and grading. The instructor action runs inside the grading
job, after setup, rather than starting another workflow or machine.

Preparation exports committed student files plus protected instructor files into
`workspace.tar.gz`. It excludes `.git`, untracked runner files and the
instructor's unlisted solution. It preserves regular-file permissions, hidden
files, spaces and Unicode. Limits are 5,000 files, 100 MiB total and 10 MiB per
student file. Unprotected symlinks/submodules are rejected. Git-dependent builds
need their own explicit setup.

Protected source packages allow 500 regular files / 2 MiB. Relative paths cannot
overlap, traverse, include `.git` or `.github`, or cross symlink ancestors.
Restoration modifies only the runner checkout, never student Git commits.

The API validates the fixed coordinator, pinned shared grading call, immutable
artifact ID handoff, grading permissions, action metadata and final score output.
Student edits to the action or configuration are rejected. The manual wrapper is
not an authorization entrypoint and cannot issue a token. Extra coordinator jobs,
arbitrary grading workflow calls and caller-selected score inputs are rejected.
Shared version tags and their approved commit/tag identities are immutable.

## Copying and verifying Grade Tokens

The final run summary displays the score, run/attempt and one fenced **Grade Token**
code block. Students copy that entire value and paste it into their submission.
No student secret or AbstractClassroom account is needed. There are no token
files, token artifacts or download links. The token is intentionally retained in
GitHub's saved summary, visible according to repository access, not raw logs.

GitHub automatically masks JWT-shaped values. New Grade Tokens use the versioned
`ACGT1_` envelope containing a base64url-encoded signed receipt. This is a
transport representation, not encryption or a new signature scheme. The API
unwraps it, then verifies the original signature and expected course/assignment.
The envelope is for intentionally shareable grade evidence, never access tokens,
pairing secrets or signing keys. Ordinary GitHub secret masking stays enabled.

The validator accepts the copied value unchanged. Previously issued signed
receipts remain verifiable. The backend stores issuance metadata and grade, not
the token string. Retries reconstruct the receipt from its original identity,
timestamp and retained signing key. No token goes in browser storage.

A token authenticates the grade reported by the registered instructor action and
binds it to a submission, source version, run and timestamp. It does not prove
student identity, authorship or honest execution of every assertion. Student code
sharing the grading job can affect tests, environment files or scoring. Tests sent
to student-owned runners are visible; hidden assessments need other infrastructure.

## Source versions and migration

`sourceVersion` is a branch/tag name, not a commit SHA. Branch snapshots can
advance. Published tags cannot move, and branch/tag name collisions are rejected.
Set a tag name in JSON before publishing that tag. A source push on another ref
waits without publishing the wrong version. The API records resolved commits.

Version `v3.0.0` moves instructor logic into the composite action, adds shared
`grading.yml`, and accepts API-issued copyable Grade Tokens. Deploy matching
backend validation/trust first, update the coordinator/action/JSON, retain the
small manual wrapper, and republish the selected instructor source. Do not edit
published tags or re-pair an already connected source.

The action under this shared repository's own `.github/actions/instructor`
is a public synthetic CI fixture, not a student assignment. CI exercises action
outputs and a non-credential Grade Token summary so rendered display can be
checked independently of token issuance.
