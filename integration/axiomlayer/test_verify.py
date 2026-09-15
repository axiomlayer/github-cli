#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import os
import shutil
import stat
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest import mock


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location(
    "axiom_github_cli_verify", HERE / "verify.py"
)
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = verify.load_manifest()

    @contextmanager
    def firewall_fixture(self) -> Iterator[tuple[Path, dict]]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = copy.deepcopy(self.manifest)
            active = manifest["workflowFirewall"]["activeWorkflow"]["path"]
            destination = root / active
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPOSITORY_ROOT / active, destination)
            for key in ("wrapperPath", "candidateWrapperPath", "derivationPath"):
                controlled_path = manifest["nix"][key]
                destination = root / controlled_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPOSITORY_ROOT / controlled_path, destination)
            for entry in manifest["workflowFirewall"]["archive"]:
                destination = root / entry["archivePath"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPOSITORY_ROOT / entry["archivePath"], destination)
            yield root, manifest

    @staticmethod
    def refresh_active_digest(manifest: dict, root: Path) -> None:
        active = manifest["workflowFirewall"]["activeWorkflow"]
        active["sha256"] = hashlib.sha256(
            (root / active["path"]).read_bytes()
        ).hexdigest()

    @staticmethod
    def remote_metadata() -> dict:
        return {
            "full_name": "axiomlayer/github-cli",
            "fork": True,
            "parent": {"full_name": "cli/cli"},
            "source": {"full_name": "cli/cli"},
            "default_branch": "trunk",
        }

    @staticmethod
    def indexed(
        path: str = ".github/workflows/axiomlayer-integration.yml",
    ) -> list[dict]:
        return [{"path": path, "state": "active"}]

    def test_manifest_is_complete(self) -> None:
        verify.validate_manifest(self.manifest)

    def test_promoted_source_is_exact(self) -> None:
        verify.verify_source(self.manifest)

    def test_archive_is_complete_and_byte_identical_to_upstream(self) -> None:
        verify.verify_workflow_archive(self.manifest)

    def test_only_active_workflow_is_read_only_and_pinned(self) -> None:
        verify.verify_active_workflow(self.manifest)

    def test_uppercase_machine_owner_is_refused(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "axiomlayer/github-cli", "AxiomLayer/github-cli", 1
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(verify.VerificationError, "source job guard"):
                verify.verify_active_workflow(manifest, root)

    def test_manual_feature_ref_is_refused(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "github.ref == 'refs/heads/trunk'",
                "github.ref == 'refs/heads/feature/unsafe'",
                1,
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(verify.VerificationError, "source job guard"):
                verify.verify_active_workflow(manifest, root)

    def test_manual_alternate_workflow_ref_is_refused(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "axiomlayer-integration.yml@refs/heads/trunk",
                "release.yml@refs/heads/trunk",
                1,
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(verify.VerificationError, "source job guard"):
                verify.verify_active_workflow(manifest, root)

    def test_cross_repository_pull_request_is_refused(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "github.event.pull_request.head.repo.full_name == "
                "'axiomlayer/github-cli'",
                "github.event.pull_request.head.repo.full_name == 'attacker/cli'",
                1,
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(verify.VerificationError, "source job guard"):
                verify.verify_active_workflow(manifest, root)

    def test_pull_request_alternate_workflow_ref_is_refused(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "axiomlayer-integration.yml@refs/pull/",
                "attacker.yml@refs/pull/",
                1,
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(verify.VerificationError, "source job guard"):
                verify.verify_active_workflow(manifest, root)

    def test_pull_request_prefix_matching_is_refused(self) -> None:
        mutations = (
            (
                "github.ref == format('refs/pull/{0}/merge', "
                "github.event.pull_request.number)",
                "startsWith(github.ref, 'refs/pull/')",
            ),
            (
                "github.workflow_ref == format('axiomlayer/github-cli/.github/"
                "workflows/axiomlayer-integration.yml@refs/pull/{0}/merge', "
                "github.event.pull_request.number)",
                "startsWith(github.workflow_ref, 'axiomlayer/github-cli/')",
            ),
        )
        for original, replacement in mutations:
            with (
                self.subTest(replacement=replacement),
                self.firewall_fixture() as (
                    root,
                    manifest,
                ),
            ):
                active = manifest["workflowFirewall"]["activeWorkflow"]
                path = root / active["path"]
                text = path.read_text(encoding="utf-8")
                self.assertIn(original, text)
                path.write_text(
                    text.replace(original, replacement, 1), encoding="utf-8"
                )
                self.refresh_active_digest(manifest, root)
                with self.assertRaisesRegex(
                    verify.VerificationError, "source job guard"
                ):
                    verify.verify_active_workflow(manifest, root)

    def test_unprotected_default_ref_is_refused(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "github.ref_protected == true",
                "github.ref_protected == false",
                1,
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(verify.VerificationError, "source job guard"):
                verify.verify_active_workflow(manifest, root)

    def test_terminal_gate_cannot_green_skip_an_untrusted_source(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "    if: always()\n", "    if: success()\n", 1
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(verify.VerificationError, "unconditional"):
                verify.verify_active_workflow(manifest, root)

    def test_terminal_gate_cannot_add_an_authority_condition(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "    if: always()\n",
                "    if: always() && github.repository == 'axiomlayer/github-cli'\n",
                1,
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(verify.VerificationError, "unconditional"):
                verify.verify_active_workflow(manifest, root)

    def test_terminal_gate_rechecks_exact_pull_request_identity(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                'test "$REF" = "refs/pull/$PR_NUMBER/merge"',
                'test "$REF" = "refs/pull/unsafe/merge"',
                1,
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(
                verify.VerificationError, "terminal authority drifted"
            ):
                verify.verify_active_workflow(manifest, root)

    def test_floating_hosted_runner_is_refused(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "runs-on: ubuntu-24.04",
                "runs-on: ubuntu-latest",
                1,
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(
                verify.VerificationError, "exact hosted job runners"
            ):
                verify.verify_active_workflow(manifest, root)

    def test_self_hosted_runner_is_refused(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "runner: ubuntu-24.04-arm",
                "runner: self-hosted",
                1,
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(
                verify.VerificationError, "exact hosted runner matrix"
            ):
                verify.verify_active_workflow(manifest, root)

    def test_checkout_must_be_clean(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "          clean: true\n", "", 1
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(verify.VerificationError, "clean checkout"):
                verify.verify_active_workflow(manifest, root)

    def test_checkout_must_use_exact_event_sha(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "          ref: ${{ github.sha }}\n", "          ref: trunk\n", 1
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(verify.VerificationError, "exact event SHA"):
                verify.verify_active_workflow(manifest, root)

    def test_local_workflow_firewall_is_complete(self) -> None:
        verify.verify_local_workflow_firewall(self.manifest)

    def test_nix_expression_repeats_reviewed_pins(self) -> None:
        expression = (HERE / "default.nix").read_text(encoding="utf-8")
        self.assertIn(self.manifest["promotion"]["version"], expression)
        self.assertIn(self.manifest["nix"]["nixpkgs"]["commit"], expression)
        self.assertIn(self.manifest["nix"]["nixpkgs"]["narHash"], expression)
        self.assertIn(self.manifest["nix"]["nixpkgs"]["ghVendorHash"], expression)
        self.assertIn('export HOME="$TMPDIR/axiomlayer-home"', expression)
        self.assertIn(
            "unset PAGER GH_PAGER NO_COLOR CLICOLOR GH_ACCESSIBLE_PROMPTER",
            expression,
        )

    def test_nix_bootstrap_is_exact_and_credential_sterile(self) -> None:
        verify.verify_nix_bootstrap(self.manifest)

    def test_nix_bootstrap_adversarial_mutations_fail_closed(self) -> None:
        mutations = {
            "digest-drift": lambda text: text + "# drift\n",
            "installer-digest": lambda text: text.replace(
                self.manifest["nix"]["installerSha256"], "0" * 64
            ),
            "populated-environment": lambda text: text.replace("env -i", "env"),
            "credential": lambda text: text + "# GITHUB_TOKEN\n",
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), self.firewall_fixture() as (root, manifest):
                path = root / manifest["nix"]["wrapperPath"]
                path.write_text(mutate(path.read_text(encoding="utf-8")))
                with self.assertRaises(verify.VerificationError):
                    verify.verify_nix_bootstrap(manifest, root)

    def test_candidate_wrapper_adversarial_mutations_fail_closed(self) -> None:
        mutations = {
            "ambient-go": lambda text: text.replace("env -i", "env", 1),
            "unsandboxed-nix": lambda text: text.replace("--option sandbox true", ""),
            "source-drift": lambda text: text.replace(
                self.manifest["promotion"]["commit"], "0" * 40
            ),
            "digest-drift": lambda text: text + "# drift\n",
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), self.firewall_fixture() as (root, manifest):
                path = root / manifest["nix"]["candidateWrapperPath"]
                path.write_text(mutate(path.read_text(encoding="utf-8")))
                with self.assertRaises(verify.VerificationError):
                    verify.verify_candidate_wrapper(manifest, root)

    def test_delegated_nix_bootstrap_fails_closed(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            text = path.read_text(encoding="utf-8").replace(
                "run: sh integration/axiomlayer/install-nix-ci.sh",
                "uses: cachix/install-nix-action@"
                "13d8dd58da0234aa297dedd986986ccb8e7f3e24",
            )
            path.write_text(text, encoding="utf-8")
            self.refresh_active_digest(manifest, root)
            reference = (
                "cachix/install-nix-action@13d8dd58da0234aa297dedd986986ccb8e7f3e24"
            )
            active["actionReferences"] = sorted(
                [*active["actionReferences"], reference]
            )
            manifest["workflowActions"]["cachix/install-nix-action"] = (
                "13d8dd58da0234aa297dedd986986ccb8e7f3e24"
            )
            with self.assertRaisesRegex(verify.VerificationError, "must use"):
                verify.verify_active_workflow(manifest, root)

    def test_missing_ocelot_surface_fails_closed(self) -> None:
        bad = copy.deepcopy(self.manifest)
        del bad["targets"]["windows-aarch64"]
        with self.assertRaises(verify.VerificationError):
            verify.validate_manifest(bad)

    def test_all_six_artifact_pins_are_immutable(self) -> None:
        for target in sorted(self.manifest["targets"]):
            with self.subTest(target=target):
                bad = copy.deepcopy(self.manifest)
                bad["targets"][target]["binarySha256"] = "0" * 64
                with self.assertRaisesRegex(verify.VerificationError, "pin diverges"):
                    verify.validate_manifest(bad)

    def test_workflow_action_inventory_cannot_be_reblessed(self) -> None:
        bad = copy.deepcopy(self.manifest)
        bad["workflowActions"]["actions/checkout"] = "0" * 40
        with self.assertRaisesRegex(verify.VerificationError, "action pins diverge"):
            verify.validate_manifest(bad)

    def test_duplicate_manifest_key_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text('{"schema":"first","schema":"second"}\n')
            with self.assertRaisesRegex(verify.VerificationError, "duplicate JSON key"):
                verify.load_manifest(path)

    def test_real_credentials_fail_closed(self) -> None:
        bad = copy.deepcopy(self.manifest)
        bad["credentials"]["required"] = ["SIGNING_KEY"]
        with self.assertRaises(verify.VerificationError):
            verify.validate_manifest(bad)

    def test_release_archive_digest_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "artifact.zip"
            artifact.write_bytes(b"tampered archive")
            with self.assertRaises(verify.VerificationError):
                verify.assert_file_digest(artifact, "0" * 64, "archive")

    def test_release_checksum_manifest_is_independently_pinned(self) -> None:
        checksums = self.manifest["releaseChecksums"]
        self.assertEqual(1971, checksums["archiveBytes"])
        self.assertEqual(
            "6b5916dffcfa6f593b1db7890f2ddc485318e99fa263acf73aa28ebb877b53cd",
            checksums["archiveSha256"],
        )

    def test_tar_path_traversal_fails_closed(self) -> None:
        import tarfile

        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "escape.tar.gz"
            payload = b"escape"
            with tarfile.open(archive, "w:gz") as source:
                member = tarfile.TarInfo("../escape")
                member.size = len(payload)
                source.addfile(member, io.BytesIO(payload))
            with self.assertRaises(verify.VerificationError):
                verify.extract_archive(archive, "tar.gz", Path(temporary) / "extract")

    def test_zip_path_traversal_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "escape.zip"
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("../escape", "escape")
            with self.assertRaises(verify.VerificationError):
                verify.extract_archive(archive, "zip", Path(temporary) / "extract")

    def test_floating_action_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow = root / ".github" / "workflows" / "ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: test\non: [push]\njobs:\n  test:\n    steps:\n"
                "      - uses: actions/checkout@v7\n",
                encoding="utf-8",
            )
            with self.assertRaises(verify.VerificationError):
                verify.verify_action_pins(root)

    def test_archive_changed_even_with_rehashed_manifest_fails_closed(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            entry = manifest["workflowFirewall"]["archive"][0]
            path = root / entry["archivePath"]
            path.write_bytes(path.read_bytes() + b"# tampered\n")
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(verify.VerificationError):
                verify.verify_workflow_archive(
                    manifest, root, provenance_root=REPOSITORY_ROOT
                )

    def test_missing_added_and_renamed_archive_files_fail_closed(self) -> None:
        for mutation in ("missing", "added", "renamed"):
            with (
                self.subTest(mutation=mutation),
                self.firewall_fixture() as (
                    root,
                    manifest,
                ),
            ):
                archive_root = (
                    root / manifest["workflowFirewall"]["provenance"]["archiveRoot"]
                )
                target = (
                    root / manifest["workflowFirewall"]["archive"][0]["archivePath"]
                )
                if mutation == "missing":
                    target.unlink()
                elif mutation == "added":
                    (archive_root / "unexpected.yml").write_text("name: extra\n")
                else:
                    target.rename(target.with_name("renamed.yml"))
                with self.assertRaises(verify.VerificationError):
                    verify.verify_workflow_archive(
                        manifest, root, provenance_root=REPOSITORY_ROOT
                    )

    def test_archive_mode_drift_fails_closed(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            entry = manifest["workflowFirewall"]["archive"][0]
            target = root / entry["archivePath"]
            target.chmod(target.stat().st_mode | stat.S_IXUSR)
            with self.assertRaises(verify.VerificationError):
                verify.verify_workflow_archive(
                    manifest, root, provenance_root=REPOSITORY_ROOT
                )

    def test_executable_extra_workflow_fails_closed(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            extra = root / ".github" / "workflows" / "extra.yml"
            extra.write_text("name: extra\non: [push]\npermissions: {}\njobs: {}\n")
            with self.assertRaises(verify.VerificationError):
                verify.verify_active_workflow(manifest, root)

    def test_active_workflow_digest_tampering_fails_closed(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]["path"]
            path = root / active
            path.write_text(path.read_text(encoding="utf-8") + "\n# drift\n")
            with self.assertRaises(verify.VerificationError):
                verify.verify_active_workflow(manifest, root)

    def test_active_workflow_digest_cannot_be_reblessed(self) -> None:
        with self.firewall_fixture() as (root, manifest):
            active = manifest["workflowFirewall"]["activeWorkflow"]
            path = root / active["path"]
            path.write_text(path.read_text(encoding="utf-8") + "\n# drift\n")
            self.refresh_active_digest(manifest, root)
            with self.assertRaisesRegex(verify.VerificationError, "re-blessed"):
                verify.verify_active_workflow(manifest, root)

    def test_active_secret_variable_environment_and_authority_fail_closed(self) -> None:
        mutations = {
            "secret": "\nenv:\n  TOKEN: ${{ secrets.REAL_TOKEN }}\n",
            "indexed-secret": "\nenv:\n  TOKEN: ${{ secrets['REAL_TOKEN'] }}\n",
            "github-token": "\nenv:\n  TOKEN: ${{ github.token }}\n",
            "indexed-github-token": "\nenv:\n  TOKEN: ${{ github['token'] }}\n",
            "variable": "\nenv:\n  SETTING: ${{ vars.REAL_SETTING }}\n",
            "indexed-variable": "\nenv:\n  SETTING: ${{ vars['REAL_SETTING'] }}\n",
            "environment": "\nenvironment: production\n",
            "write": "\npermissions:\n  contents: write\n",
            "oidc": "\npermissions:\n  id-token: write\n",
            "packages": "\npermissions:\n  packages: write\n",
            "deployment": "\npermissions:\n  deployments: write\n",
            "target-trigger": "\npull_request_target:\n",
            "publisher": "\njobs:\n  bad:\n    steps:\n      - run: git push origin trunk\n",
            "continue": "\ncontinue-on-error: true\n",
        }
        for label, payload in mutations.items():
            with self.subTest(label=label), self.firewall_fixture() as (root, manifest):
                active = manifest["workflowFirewall"]["activeWorkflow"]["path"]
                path = root / active
                path.write_text(path.read_text(encoding="utf-8") + payload)
                self.refresh_active_digest(manifest, root)
                with self.assertRaises(verify.VerificationError):
                    verify.verify_active_workflow(manifest, root)

    def test_binary_execution_environment_is_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(
                os.environ,
                {"GH_TOKEN": "fake", "ACTIONS_RUNTIME_TOKEN": "fake"},
                clear=False,
            ):
                environment = verify.sterile_execution_environment(Path(temporary))
        self.assertNotIn("GH_TOKEN", environment)
        self.assertNotIn("ACTIONS_RUNTIME_TOKEN", environment)
        self.assertEqual("false", environment["GH_TELEMETRY"])

    def test_checkout_persisted_credentials_fail_closed(self) -> None:
        replacements = (
            "",
            "persist-credentials: true",
            "persist-credentials: false\n          persist-credentials: true",
        )
        for replacement in replacements:
            with (
                self.subTest(replacement=replacement),
                self.firewall_fixture() as (
                    root,
                    manifest,
                ),
            ):
                active = manifest["workflowFirewall"]["activeWorkflow"]["path"]
                path = root / active
                text = path.read_text(encoding="utf-8").replace(
                    "persist-credentials: false", replacement, 1
                )
                path.write_text(text, encoding="utf-8")
                self.refresh_active_digest(manifest, root)
                with self.assertRaises(verify.VerificationError):
                    verify.verify_active_workflow(manifest, root)

    def test_remote_prepublication_accepts_empty_or_strict_index(self) -> None:
        metadata = self.remote_metadata()
        verify.verify_remote_workflow_state(
            self.manifest, metadata, [], "prepublication"
        )
        verify.verify_remote_workflow_state(
            self.manifest, metadata, self.indexed(), "prepublication"
        )

    def test_remote_strict_requires_exact_single_workflow(self) -> None:
        metadata = self.remote_metadata()
        verify.verify_remote_workflow_state(
            self.manifest, metadata, self.indexed(), "strict-postpublication"
        )
        for workflows in ([], self.indexed(".github/workflows/extra.yml")):
            with self.assertRaises(verify.VerificationError):
                verify.verify_remote_workflow_state(
                    self.manifest, metadata, workflows, "strict-postpublication"
                )

    def test_remote_topology_drift_fails_closed(self) -> None:
        mutations = {
            "wrong-name": ("full_name", "attacker/github-cli"),
            "uppercase-name": ("full_name", "AxiomLayer/github-cli"),
            "not-a-fork": ("fork", False),
            "wrong-parent": ("parent", {"full_name": "attacker/cli"}),
            "wrong-source": ("source", {"full_name": "attacker/cli"}),
            "wrong-default": ("default_branch", "main"),
        }
        for label, (field, value) in mutations.items():
            with self.subTest(label=label):
                metadata = self.remote_metadata()
                metadata[field] = value
                with self.assertRaises(verify.VerificationError):
                    verify.verify_remote_workflow_state(
                        self.manifest,
                        metadata,
                        self.indexed(),
                        "strict-postpublication",
                    )

    def test_remote_extra_or_inactive_workflow_fails_closed(self) -> None:
        metadata = self.remote_metadata()
        cases = [
            self.indexed() + self.indexed(".github/workflows/extra.yml"),
            [{"path": self.indexed()[0]["path"], "state": "disabled_manually"}],
        ]
        for workflows in cases:
            with self.assertRaises(verify.VerificationError):
                verify.verify_remote_workflow_state(
                    self.manifest, metadata, workflows, "prepublication"
                )

    def test_materialized_source_is_the_pin_not_the_integration_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "source"
            verify.materialize_source(self.manifest, destination)
            go_mod = (destination / "go.mod").read_text(encoding="utf-8")
            self.assertIn("toolchain go1.26.8", go_mod)
            self.assertFalse((destination / "integration" / "axiomlayer").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
