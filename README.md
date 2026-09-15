# AbstractClassroom Code Assignments workflows

Shared source verification, file restoration, and JWT issuance. This repository
contains AbstractClassroom's implementation. The
[instructor starter](https://github.com/abstractclassroom/code-assignments-template)
contains only these three files:

```text
.github/workflows/auto-grading-workflow.yml
.github/workflows/instructor_autograder.yml
.github/workflows/auto-grading-config.json
```

**Keep all three filenames unchanged.** `auto-grading-workflow.yml` coordinates
source verification and JWT issuance. Instructors edit `instructor_autograder.yml`
for tests and scoring. Both workflows and the JSON are verified against the source.

## Instructor setup

1. [Register a Code Assignments course](https://preview.abstractclassroom.com/code-assignments/register/)
   and create an assignment. The subscription is $5/course/month, with no free trial.
2. Create your instructor repository from the starter. Set its JSON:

   ```json
   {
     "assignmentId": "YOUR_ASSIGNMENT_ID",
     "files": ["tests", "build-config"],
     "sourceVersion": "main"
   }
   ```

   Replace the ID with the public assignment ID from the dashboard. `files` can
   list individual files or entire directories. Use an empty list if none need
   replacement. Both workflows and JSON are checked automatically; do not list them.
3. Add your starter code and tests. Put build/test steps in `instructor_autograder.yml`,
   **after the restore step and before Save final score**. Use any tools you need.
   Set the grading timeout (the starter uses 10 minutes; up to 60 is supported).
   Update the `score` environment variable as checks earn points, persisting it
   through `$GITHUB_ENV`. The final JWT contains that value as `grade`. You choose
   the point scale and how expected test failures contribute to partial credit.
   Unexpected workflow errors/timeouts still prevent issuance. The empty starter
   performs no assessment and reports zero until you add grading steps.
4. Save the private pairing token from the dashboard as the instructor repository's
   Actions secret `ABSTRACTCLASSROOM_SOURCE_TOKEN`. It expires after two hours and
   is consumed at first source pairing. Never commit it or copy it to students.
5. Push the branch or tag named by `sourceVersion`, or dispatch **Auto grading**
   against it. The registered instructor repository publishes its files to
   AbstractClassroom. Later runs use short-lived GitHub OIDC automatically.
   A private source repository works without a GitHub App or a stored GitHub token.
6. Enable **Template repository** in GitHub settings and share it with students.
   Keep all three workflow/configuration files identical to the selected source.

## Scoring

The instructor workflow initializes the environment variable `score` to zero. Each instructor
step can add points and persist the new value for subsequent steps:

```yaml
      - name: Score the first test
        shell: bash
        run: |
          if ./test-one; then
            score=$((score + 10))
          fi
          printf 'score=%s\n' "$score" >> "$GITHUB_ENV"
      - name: Score the second test
        shell: bash
        run: |
          if ./test-two; then
            score=$((score + 20))
          fi
          printf 'score=%s\n' "$score" >> "$GITHUB_ENV"
```

These are instructor examples, not files included in the starter. Use your own
commands, libraries and score calculations; decimals are supported too.
Writing `$GITHUB_ENV` affects subsequent steps. Within the current shell, update
`score` as shown before persisting it. A plain `export` does not survive the step.

Keep **Save final score** last in `grading`. It exports `score` as a job output,
which passes through the coordinator to the JWT workflow. Keep the JWT coordinator intact; no scoring code belongs there. The signed claim is a JSON
number named `grade`, with no assumed maximum of 100, percentage sign, rounding,
or platform-selected passing threshold. Scores must be finite, nonnegative and
at most JavaScript's maximum safe number (9007199254740991). Missing, malformed,
negative or non-finite scores are rejected. Zero is a valid grade.

Partial grades are supported when the instructor deliberately handles a failed
test and completes the grading job successfully, as in the examples above. Decide
which failures mean zero points and which should stop grading. A failed, canceled
or timed-out job produces no receipt. The JWT records `result: "graded"`; it does
not claim every test passed. Existing receipts without a grade remain verifiable.

The score is reported by the verified workflow, not independently recomputed by
AbstractClassroom. Student code running in the same job can affect environment
files, job outputs or the scoring process. Signing a grade authenticates the
reported value and source binding; it does not eliminate that interference.

## Source branches and versions

`sourceVersion` is a name, not a commit SHA. Use `main`, another branch such as
`course/fall-2026`, or a version tag such as `v1.0.0`.

For a tag, set `sourceVersion` to the tag name **before creating and pushing that
tag**. A push on another instructor branch reports that source publication is
waiting; it does not accidentally publish that branch as the requested version.
Branches publish new snapshots as they advance. Published version tags are fixed:
move to a new tag name to change a version. A branch and tag cannot share the same
published source name for one assignment. Multiple named sources can coexist.

AbstractClassroom resolves the selected name through the registered repository's
signed workflow run and records the actual commit internally. Receipts record both
the friendly source version and the resolved commit. Updating `main` does not
invalidate a run using an unchanged version tag.

## What the workflow does

- `source` verifies both original workflows and the JSON, identifies the selected registered
  source, and provides its immutable snapshot to the grading job.
- The restore step **deletes each listed student path completely and replaces it**
  from that source before instructor steps run. Other student files are left alone.
  The YAML and JSON are checked before restoration and are never overwritten to
  conceal a mismatch. Changes are confined to the Actions checkout, not committed
  back to GitHub.
- The coordinator calls `instructor_autograder.yml` for the instructor's native GitHub Actions steps. There is no
  separate grading-policy JSON, required container, build system, or report format.
- The final `jwt` job runs separately after successful grading. AbstractClassroom
  recomputes the original workflow hash there, verifies its source binding, and
  includes the final numeric `grade` in an assignment-specific `.ast` token.
  Signing keys stay on the server.

Keep the coordinator wiring and the instructor workflow's supplied `grading` job.
Add your steps between restoration and the final score export; extra grading jobs
or additional reusable workflows are not supported by this initial contract.
The backend validates both workflow contracts, the final JWT dependency chain, first restore step, and
final score output wiring,
so the JWT cannot be requested by an unrelated job or a caller-supplied pass flag.
The shared workflows use version tags; the server also checks the approved resolved
workflow commit and annotated tag object. Published AbstractClassroom version
tags must never be moved.

Listed paths must be ordinary relative paths, cannot overlap or include `.git` or
`.github`, and must exist in the instructor source. Source packages are limited to
500 files and 2 MiB. Listed source files must be regular files, not symlinks or
submodules. A symlink ancestor that would redirect restoration is rejected.

## Student and TA flow

Students create a repository from the instructor template, complete their work,
and push. No AbstractClassroom student account or secret is needed. Their Actions
allowance pays for the run. At the bottom of the workflow run summary, the final
JWT job displays the score, run ID and attempt, and **gradetoken.ast**. Copy its token
from the code block into a file named `gradetoken.ast` and submit it through the LMS.
The summary also links to the `gradetoken-<attempt>` ZIP artifact containing that
same `gradetoken.ast` file. Artifacts are retained for 30 days. Token content is
intentionally displayed in the run summary; its visibility follows repository
access. It is not printed in raw action logs. Source-only instructor runs publish
files and do not create student grade tokens.

The dashboard provides an assignment-specific TA validator link. Verification
requires the expected course and assignment. Existing receipts remain verifiable
after cancellation or signing-key rotation. The LMS identifies the submitter.

A receipt attests the numeric grade reported by the registered workflow. It does not
prove authorship, student identity, or honest execution of every assertion.
Arbitrary student code can interfere with tests or the score in its grading job; this service
does not independently inspect or count assertions or validate test reports.
Tests delivered to student-owned runners are visible, including tests published
from a private instructor repository. Hidden assessments need separate infrastructure.

## Troubleshooting and migration

- Keep the three filenames and the instructor's YAML/JSON bytes unchanged in student
  repositories. Editing the file list or workflow causes verification to fail.
- A source-version error means the instructor has not published that exact branch
  or tag yet. Push/run the named ref. Use a new tag for a changed tagged version.
- A pairing error means the instructor must save a valid, unexpired pairing secret.
  Generate a new one from the assignment dashboard if needed. It is unnecessary
  after the repository's immutable ID has been registered.
- A source update during grading requires **Re-run all jobs**. Partial reruns cannot
  reuse an earlier attempt's authorization. Every run/attempt is independently bound.
- A failed, canceled, or timed-out grading job does not issue a new token. The
  instructor controls conditional steps and what their test commands consider success.
- HTTP 402 means the course needs a paid subscription or an operator waiver.
- To migrate from the old template, remove only the old AbstractClassroom scaffold,
  including `.abstractclassroom`, the old workflow/runtime/test examples, and sample
  `solution.sh`. Retain your own code. Install the three new files, set the existing
  assignment ID, choose your files/source version, and put your own commands in YAML.

## Development

Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`.
Tests create disposable Git fixtures; they do not publish courses or charge cards.
Server authorization tests live in `abstract_classroom_lambdas`.

GitHub documents [reusable-workflow OIDC identity](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows)
and [immutable workflow artifacts](https://docs.github.com/en/actions/tutorials/store-and-share-data).
