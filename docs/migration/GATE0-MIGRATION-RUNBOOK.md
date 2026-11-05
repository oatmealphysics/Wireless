# Gate 0 repository migration runbook

Date: 2026-11-04  
Maintenance owner: Codex  
Independent reviewer: Claude  
Source writer freeze: active for the legacy `poc-research` checkout

## Purpose and authority

This runbook controls the one-time migration from the oversized, divergent
legacy repositories to the new `Wireless` repositories. The operator approved
replacement of the new repositories' primary branches for this migration.
After Gate 0 closes, force-pushes and further history rewrites are prohibited.

GitHub is the sole write authority:

- primary: `https://github.com/oatmealphysics/Wireless.git`
- deterministic mirror: `git@gitlab01.an.dd:user08/Wireless.git`
- intended active refs: `refs/heads/main` only
- intended tags: none

The old `poc-research` remotes are archives, not publication targets. Do not
retry the old 5+ GiB push and do not add a second push URL to `origin`.

## Frozen source and archive

The final legacy source state selected for migration is:

- commit: `ed823d4c1d7c80e51201df80b4c02d039a22488d`
- tree: `2ead47a97127e2804fe64030c2edfe1ead63fd46`
- branch: legacy `main`

The recoverable offline archive is outside the active history at
`/home/borealis/poc-research/work/gate0-archive-20261104/`.

| Artifact | Verification |
| --- | --- |
| `full-history.bundle` | 94 refs; complete bundle through legacy `main` `92f3ae33dab9807c4526e66c0e4a4258a1c009cf`; SHA-256 `dffa66c4bca5c45be59f545d038ff0ee175565a42c2a084a7a4e3bb96e217a36` |
| `post-full-bundle-main-92f3ae3-to-ed823d4.bundle` | incremental main history through the frozen tip; requires `92f3ae33dab9807c4526e66c0e4a4258a1c009cf`; SHA-256 `03620c26159895d889aeff5a68230db875d1012d3a1ccb18b3cd42bc4d9a7f12` |
| `lfs/` | complete retained legacy LFS object store copied before cleanup |
| `captures/` | raw IQ files, SigMF metadata, sanitized derivatives, README, and `SHA256SUMS.txt` copied before cleanup |

The archive occupied approximately 16 GiB when verified. Verify both bundles
before relying on them:

```sh
git -C /home/borealis/poc-research bundle verify \
  work/gate0-archive-20261104/full-history.bundle
git -C /home/borealis/poc-research bundle verify \
  work/gate0-archive-20261104/post-full-bundle-main-92f3ae3-to-ed823d4.bundle
sha256sum /home/borealis/poc-research/work/gate0-archive-20261104/*.bundle
sha256sum /home/borealis/poc-research/work/gate0-archive-20261104/captures/*
```

The tracked capture manifest remains at `captures/SHA256SUMS.txt`. It records
the raw IQ hashes without storing the IQ payloads in active Git history.

## Clean-history construction rules

The candidate was rebuilt from the reviewed cleaned lineage, not from the
legacy merge tip. The cleanup rules are:

1. Remove the entire historical `captures/` subtree from every rewritten
   commit.
2. Restore only the reviewed README, checksum manifest, SigMF metadata, and
   sanitized JSONL derivatives in commit `1f61b9e`.
3. Never restore `.iq`, `.cf32`, `.complex8`, `.sigmf-data`, `.cfile`, or raw
   private device data.
4. Remove the obsolete Git-LFS rule for IQ files; `git lfs ls-files` must be
   empty in the cleaned repository.
5. Preserve firmware/reference artifacts already covered by
   `firmware/SHA256SUMS` and `firmware/artifact-register.json`.
6. Replay the seven writer-frozen source commits after legacy `92f3ae3` as
   patches, preserving their authorship and intended trees without importing
   the legacy ancestry.
7. Publish only `main`; legacy remote-tracking branches, stash refs, rehearsal
   refs, and old GitHub topic branches are archive-only.

The cleaned lineage is expected to have a roughly 252 MiB packed object store,
with no reachable blob over 100 MiB.

## Candidate preflight

Run from the `Wireless` checkout:

```sh
git status --short --branch
git fsck --full --strict
git count-objects -vH
git lfs ls-files
git rev-list --objects --all |
  git cat-file --batch-check='%(objecttype) %(objectsize) %(objectname) %(rest)' |
  awk '$1 == "blob" && $2 > 100000000 { print; bad=1 } END { exit bad }'
git rev-list --objects --all |
  grep -E 'captures/.*\.(iq|cf32|complex8|sigmf-data|cfile)$' && exit 1 || true
python3 -B tools/validate-project.py
python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -v
```

Record `git rev-parse HEAD`, `git rev-parse HEAD^{tree}`, and
`git rev-list --all --count`. An empty result from both the LFS and forbidden
capture-path checks is required.

## One-time publication

Before publication, independently verify the archive, rules, frozen-tree
preservation, object-size result, and rollback path. Confirm no writer has
advanced the frozen source.

Configure exactly one push URL for each remote:

```sh
git remote remove origin 2>/dev/null || true
git remote remove mirror 2>/dev/null || true
git remote add origin git@github.com:oatmealphysics/Wireless.git
git remote add mirror git@gitlab01.an.dd:user08/Wireless.git
git remote get-url --all --push origin
git remote get-url --all --push mirror
```

GitHub was created empty. GitLab was initialized at
`6838bebd5c08f229da0753ae8bcc1cb224e5b55f`. Within the approved maintenance
window only:

```sh
git push --set-upstream origin main
git push --force-with-lease=main:6838bebd5c08f229da0753ae8bcc1cb224e5b55f \
  mirror main:main
```

Configure GitLab repository mirroring to pull `main` from the GitHub primary.
Do not configure GitHub as a push mirror to GitLab and do not add GitLab as a
second `origin` push URL. After the initial replacement, all replication must
be fast-forward-only.

## Fresh-clone verification

Use two new, empty temporary directories:

```sh
git clone git@github.com:oatmealphysics/Wireless.git /tmp/wireless-github-check
git clone git@gitlab01.an.dd:user08/Wireless.git /tmp/wireless-gitlab-check
git -C /tmp/wireless-github-check fsck --full --strict
git -C /tmp/wireless-gitlab-check fsck --full --strict
git -C /tmp/wireless-github-check rev-parse HEAD HEAD^{tree}
git -C /tmp/wireless-gitlab-check rev-parse HEAD HEAD^{tree}
git -C /tmp/wireless-github-check rev-list --all --count
git -C /tmp/wireless-gitlab-check rev-list --all --count
git ls-remote --heads --tags git@github.com:oatmealphysics/Wireless.git
git ls-remote --heads --tags git@gitlab01.an.dd:user08/Wireless.git
```

The exposed refs, `main` tip, tree, and commit count must match. Run the
maintained offline validator and test suites in both clones. Hosted CI is
reported literally as `NOT RUN` until a workflow run actually executes.

## Rollback

The legacy repository and both verified bundles remain untouched throughout
the maintenance window. If publication fails before both fresh clones verify:

1. stop all writers and disable repository mirroring;
2. retain the failed remote tips as incident evidence;
3. restore GitLab's initialization tip only with the recorded lease shown
   above, or recreate either new repository from the verified archive;
4. do not change or garbage-collect the offline archive; and
5. reopen Gate 0 and record the exact failure in `SESSION-NOTES.md`.

Once both services and fresh clones verify and the independent reviewer
accepts the result, close the one-time rewrite exception. Subsequent recovery
uses revert commits or a new forward commit, never force-push or history
rewrite.
