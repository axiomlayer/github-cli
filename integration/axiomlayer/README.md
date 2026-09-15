# AxiomLayer GitHub CLI integration lane

`axiomlayer/github-cli` is a true GitHub fork of `cli/cli`. Upstream remains the
source of GitHub CLI and is never modified by this layer. The fork adds only the
fleet's integration evidence around the exact candidate selected by
`axiomlayer/dotfiles` PR #49.

The candidate is GitHub CLI 2.100.0 at
`45437bc7eeeb3359bbfddd1742f79de7652fd3e2`. The `v2.100.0` tag resolves
directly to that commit and its tree is pinned separately. The integration
manifest repeats the six device-facing archive and extracted-binary SHA-256
values from the Dotfiles promotion verifier. It also records release sizes from
GitHub's immutable release-asset metadata so truncated downloads fail before
extraction.

## Evidence boundary

- Linux x86_64/ARM64 and macOS x86_64/ARM64 materialize the exact commit, test
  the complete suite with Go 1.26.8, then build it with Nix 2.35.2 and the
  digest-locked Nixpkgs revision from Dotfiles #49. The Nix build also runs a
  sandboxed deterministic test seam. The GitHub CLI dependency hash is the one
  reviewed in that exact Nixpkgs revision.
- Windows x86_64/ARM64 materialize the same commit, then build and test it with
  the source-declared Go 1.26.8 toolchain. Native Nix on Windows is explicitly
  unsupported; it is neither emulated nor silently skipped.
- Every native job downloads the matching upstream 2.100.0 release asset,
  verifies its size and archive SHA-256, safely extracts it, verifies the
  device-consumed binary SHA-256, executes `gh --version`, and writes a local
  receipt bound to the candidate and target.
- WSL x86_64/ARM64 cannot be represented by a GitHub-hosted runner. Ocelot and
  Siberian must produce downstream physical acceptance receipts using the
  matching Linux artifacts.
- Availability at `install.axiomlayer.com` is a downstream promotion property.
  This lane proves the exact upstream bytes named by that contract; the
  Dotfiles promotion lane must separately prove that the mirror serves those
  same bytes before a device consumes them.

The AxiomLayer workflow is the only executable YAML under `.github/workflows`.
It has only `contents: read`, references no secret, variable, or environment,
persists no checkout credential, and publishes nothing. Its two distinct
Actions are pinned to full commits. Nix is installed only through the shared
fleet wrapper, which pins the official launcher and all four native tarball
digests and executes the launcher with its credential environment removed.
Every source job additionally binds execution to the exact lowercase
`axiomlayer/github-cli` identity. Pull requests must originate in that same
repository, target `trunk`, and execute both the source and workflow from the
exact `refs/pull/<event-number>/merge` identity. Push, scheduled, and manual
runs require the protected `trunk` ref and the exact default-branch identity of
this workflow. Each checkout starts from the exact event SHA with a clean tree
and credentials disabled, so a stale hosted workspace cannot alter the
candidate under test. An unconditional terminal authority job reruns the event
checks and requires every source job to report success; an invalid event cannot
produce a green workflow by causing guarded jobs to skip.

The workflow, Nix installer wrapper, Nix derivation, and candidate-execution
wrapper are independently digest-bound in the verifier rather than being
self-authorized by editable manifest values. Go tests, Windows builds, Nix
evaluation/builds, and published-binary version probes run with explicit
environment allowlists. The Nix build also requires sandboxing. Hosted runtime
credentials, shell startup files, proxy credentials, and repository-provided
environment values do not enter candidate execution.

The 14 inherited upstream workflows and their eight support files are preserved
outside GitHub's executable workflow directory at
`integration/axiomlayer/upstream-workflows`. Every archived file is compared
byte-for-byte with `cli/cli` commit
`38316c1c4f275030e3df6666382922e75410d68b`. The manifest records both paths,
SHA-256, kind, Action references, and every referenced secret, variable, and
deployment environment. Updating the manifest digest cannot bless modified
archive bytes because the verifier independently reads the pinned Git blob.

Remote verification has two explicit modes. `prepublication` accepts the
current empty GitHub workflow index or the final singleton while still proving
the repository is a true `cli/cli` fork with default branch `trunk`.
`strict-postpublication` requires GitHub to index exactly the single AxiomLayer
workflow in the active state. Pull requests use `prepublication`; pushes,
schedules, and manual runs use `strict-postpublication`, so the relaxed mode
cannot become the steady-state policy. Neither mode changes repository settings.

## Local checks

```sh
python3 integration/axiomlayer/verify.py validate
python3 integration/axiomlayer/verify.py verify-source
python3 integration/axiomlayer/verify.py verify-nix-bootstrap
python3 integration/axiomlayer/verify.py verify-workflows \
  --remote-mode prepublication
python3 -m unittest -v integration/axiomlayer/test_verify.py
python3 integration/axiomlayer/verify.py verify-all-assets \
  --directory /tmp/axiomlayer-github-cli-assets
```

After the firewall reaches the default branch and GitHub has indexed it, run:

```sh
python3 integration/axiomlayer/verify.py verify-workflows \
  --remote-mode strict-postpublication
```

The Nix derivation accepts a materialized copy of the promoted source:

```sh
python3 integration/axiomlayer/verify.py materialize-source \
  --destination /tmp/github-cli-2.100.0
nix-build integration/axiomlayer/default.nix \
  --arg src /tmp/github-cli-2.100.0 \
  --argstr system "$(nix eval --impure --raw --expr builtins.currentSystem)" \
  --no-out-link
```

This is a build-and-test integration lane. It does not mirror, upload, tag,
release, deploy, attest, sign, or authenticate to GitHub.
