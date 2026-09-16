#!/usr/bin/env python3
"""Fail-closed verifier for AxiomLayer's pinned GitHub CLI lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[1]
DEFAULT_MANIFEST = HERE / "github-cli-2.100.0.json"
INTEGRATION_WORKFLOW = Path(".github/workflows/axiomlayer-integration.yml")
HEX_COMMIT = re.compile(r"^[0-9a-f]{40}$")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
REMOTE_USE = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^'\"\s#]+)", re.MULTILINE)
CONTEXT_REFERENCE = {
    "secrets": re.compile(
        r"(?<![-A-Za-z0-9_])secrets(?:\.([A-Za-z_][A-Za-z0-9_]*)|"
        r"\s*\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\])"
    ),
    "variables": re.compile(
        r"(?<![-A-Za-z0-9_])vars(?:\.([A-Za-z_][A-Za-z0-9_]*)|"
        r"\s*\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\])"
    ),
}
WORKFLOW_SUFFIXES = {".yml", ".yaml"}
REMOTE_MODES = {"prepublication", "strict-postpublication"}
WORKFLOW_PROVENANCE_COMMIT = "38316c1c4f275030e3df6666382922e75410d68b"
EXPECTED_WORKFLOW_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-go": "b7ad1dad31e06c5925ef5d2fc7ad053ef454303e",
}
EXPECTED_TARGET_SPECS = {
    "darwin-aarch64": {
        "runner": "macos-15",
        "nixSystem": "aarch64-darwin",
        "buildMode": "nix-native",
        "format": "zip",
        "archiveName": "gh_2.100.0_macOS_arm64.zip",
        "archiveBytes": 14212224,
        "archiveRoot": "gh_2.100.0_macOS_arm64",
        "binary": "bin/gh",
        "archiveSha256": "45f9a62da2f6e641a7fad57e2ce39656dfd7ef331372d80a2a2aed65abb01642",
        "binarySha256": "51f1bd7ed1724774d2c1d91fb4efdb676d3eb200ce95c0021c8544fe7435bc13",
    },
    "darwin-x86_64": {
        "runner": "macos-15-intel",
        "nixSystem": "x86_64-darwin",
        "buildMode": "nix-native",
        "format": "zip",
        "archiveName": "gh_2.100.0_macOS_amd64.zip",
        "archiveBytes": 15818005,
        "archiveRoot": "gh_2.100.0_macOS_amd64",
        "binary": "bin/gh",
        "archiveSha256": "fcd7799e85eb575f3c7d2b1679bfbfedaefa1269d4bc7d096b51e10939b4812b",
        "binarySha256": "43f9c5f8fb3e6ed097bff7d1b7b86158cddddd4fc6b6704b6e1c3c2b7d35dcbd",
    },
    "linux-aarch64": {
        "runner": "ubuntu-24.04-arm",
        "nixSystem": "aarch64-linux",
        "buildMode": "nix-native",
        "format": "tar.gz",
        "archiveName": "gh_2.100.0_linux_arm64.tar.gz",
        "archiveBytes": 13783869,
        "archiveRoot": "gh_2.100.0_linux_arm64",
        "binary": "bin/gh",
        "archiveSha256": "ea4e7a581a32ccad6cc7923cb1576ac5859ba4b9a16ab22eb8f8a96e78e2e961",
        "binarySha256": "28a037b967065aa314cb6d539943b55d27ef2f97c523ab2b6023ccf284e1828d",
    },
    "linux-x86_64": {
        "runner": "ubuntu-24.04",
        "nixSystem": "x86_64-linux",
        "buildMode": "nix-native",
        "format": "tar.gz",
        "archiveName": "gh_2.100.0_linux_amd64.tar.gz",
        "archiveBytes": 15152253,
        "archiveRoot": "gh_2.100.0_linux_amd64",
        "binary": "bin/gh",
        "archiveSha256": "e4d4bb4498e8d007abe545b6568926793ace1b6447da598294a610018cb164be",
        "binarySha256": "553949e2efa12842771efe6012aa4de21f1d591530ec17fc435f610f10e017ee",
    },
    "windows-aarch64": {
        "runner": "windows-11-arm",
        "nixSystem": None,
        "buildMode": "go-native",
        "format": "zip",
        "archiveName": "gh_2.100.0_windows_arm64.zip",
        "archiveBytes": 13746870,
        "archiveRoot": "",
        "binary": "bin/gh.exe",
        "archiveSha256": "7beaeb4743cf255809a8e574a2724c685b566545e04eabea284fe38a56c15b02",
        "binarySha256": "d36ec6d8429409592dc3a58f169c2c38d986af6f232edf8ddebb6020dfb1dab1",
    },
    "windows-x86_64": {
        "runner": "windows-2025",
        "nixSystem": None,
        "buildMode": "go-native",
        "format": "zip",
        "archiveName": "gh_2.100.0_windows_amd64.zip",
        "archiveBytes": 15326700,
        "archiveRoot": "",
        "binary": "bin/gh.exe",
        "archiveSha256": "227e35230b25db3fa1b997bab7cf4d67df0470a3b75b99e4ee66bce1a7cd4e72",
        "binarySha256": "2ae2b350c227a618f2d8965b1900aeee13446ff42e17ef0bd5a0b6405c593cfb",
    },
}
EXPECTED_NIX_SYSTEMS = {
    "aarch64-darwin",
    "aarch64-linux",
    "x86_64-darwin",
    "x86_64-linux",
}
EXPECTED_JOB_RUNNERS = [
    "ubuntu-24.04",
    "${{ matrix.runner }}",
    "ubuntu-24.04",
]
EXPECTED_MATRIX_RUNNERS = [
    "macos-15",
    "macos-15-intel",
    "ubuntu-24.04-arm",
    "ubuntu-24.04",
    "windows-11-arm",
    "windows-2025",
]
EXPECTED_ACTIVE_WORKFLOW_SHA256 = (
    "f4a5fe55e9af8b732717ab354b7a75ec73556c2e3c5adbcbd55d1bb98d680fc1"
)
EXPECTED_NIX_WRAPPER_SHA256 = (
    "e636dfdf728696f8485d004daa2d4169fbc568c2ad3e5c5aa5b75ef528d4d3de"
)
EXPECTED_CANDIDATE_WRAPPER_SHA256 = (
    "d0356c750805c88cc84f6231bf61a0f166bc667666bcc672b8acd14bdafa15d9"
)
EXPECTED_DERIVATION_SHA256 = (
    "8b03239a6fed19a189d6644ad3aaa5d43a742f148a2fb549f4a297324a1f7232"
)
TRUSTED_WORKFLOW_CONDITION = (
    "github.repository == 'axiomlayer/github-cli' && "
    "((github.event_name == 'pull_request' && "
    "github.event.pull_request.head.repo.full_name == 'axiomlayer/github-cli' && "
    "github.base_ref == 'trunk' && github.ref == "
    "format('refs/pull/{0}/merge', github.event.pull_request.number) && "
    "github.workflow_ref == format('axiomlayer/github-cli/.github/workflows/"
    "axiomlayer-integration.yml@refs/pull/{0}/merge', "
    "github.event.pull_request.number)) || "
    "((github.event_name == 'push' || github.event_name == 'schedule' || "
    "github.event_name == 'workflow_dispatch') && github.ref == "
    "'refs/heads/trunk' && github.ref_protected == true && "
    "github.workflow_ref == 'axiomlayer/github-cli/.github/workflows/"
    "axiomlayer-integration.yml@refs/heads/trunk'))"
)
EXPECTED_PROMOTION = {
    "authority": "axiomlayer/dotfiles#49",
    "authorityCommit": "ee9c1a27542f7918a30d9e9e33155baea2b96c8a",
    "authorityPolicySha256": "f566c46f4f77a21756ec4684eaefbdb599b1fc1f258298f905ab4e375380492b",
    "authorityVerifierSha256": "88f6debae07f8166a0978c9be06c5fb6fa1a914bf67a65ab32e7f63281eafc8c",
    "authorityRepository": "axiomlayer/github-cli",
    "forkRepository": "axiomlayer/github-cli",
    "upstreamRepository": "cli/cli",
    "commit": "45437bc7eeeb3359bbfddd1742f79de7652fd3e2",
    "tree": "ffcb605f91e5f6b83cfb83003c0fdbe68b4c9e72",
    "tag": "v2.100.0",
    "version": "2.100.0",
    "goDirective": "1.26.0",
    "goToolchain": "1.26.8",
    "sourceDateEpoch": 1788449059,
    "releaseOrigin": "https://github.com/cli/cli/releases/download/v2.100.0",
    "deliveryOrigin": "https://install.axiomlayer.com/bootstrap/gh/2.100.0",
}


class VerificationError(RuntimeError):
    """The integration contract was not proven."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def run_git(*args: str, root: Path = REPOSITORY_ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise VerificationError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise VerificationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys
        )
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise VerificationError("manifest root must be an object")
    return value


def require_sorted_unique_strings(value: Any, label: str) -> list[str]:
    require(isinstance(value, list), f"{label} must be a list")
    require(
        all(isinstance(item, str) and item for item in value), f"{label} is invalid"
    )
    require(value == sorted(set(value)), f"{label} must be sorted and unique")
    return value


def require_safe_repository_path(value: Any, label: str) -> str:
    require(isinstance(value, str) and value, f"{label} must be a path")
    require("\\" not in value, f"{label} must use POSIX separators")
    pure = PurePosixPath(value)
    require(not pure.is_absolute(), f"{label} must be repository-relative")
    require(".." not in pure.parts, f"{label} escapes the repository")
    require(pure.as_posix() == value, f"{label} is not normalized")
    return value


def require_regular_repository_file(
    root: Path, relative: str, label: str, expected_mode: int | None = None
) -> Path:
    normalized = require_safe_repository_path(relative, label)
    current = root
    for part in PurePosixPath(normalized).parts:
        current = current / part
        require(not current.is_symlink(), f"{label} contains a symlink: {current}")
    require(current.is_file(), f"{label} is missing: {current}")
    if expected_mode is not None:
        actual_mode = stat.S_IMODE(current.stat().st_mode)
        require(
            actual_mode == expected_mode,
            f"{label} mode is {actual_mode:o}, expected {expected_mode:o}",
        )
    return current


def validate_workflow_firewall_manifest(manifest: dict[str, Any]) -> None:
    firewall = manifest.get("workflowFirewall")
    require(isinstance(firewall, dict), "workflowFirewall must be an object")

    provenance = firewall.get("provenance")
    require(isinstance(provenance, dict), "workflowFirewall.provenance is missing")
    require(provenance.get("repository") == "cli/cli", "bad workflow provenance repo")
    require(
        provenance.get("commit") == WORKFLOW_PROVENANCE_COMMIT,
        "bad workflow provenance commit",
    )
    require(
        provenance.get("sourceRoot") == ".github/workflows",
        "bad upstream workflow source root",
    )
    require(
        provenance.get("archiveRoot") == "integration/axiomlayer/upstream-workflows",
        "bad upstream workflow archive root",
    )

    active = firewall.get("activeWorkflow")
    require(isinstance(active, dict), "workflowFirewall.activeWorkflow is missing")
    active_path = require_safe_repository_path(
        active.get("path"), "active workflow path"
    )
    require(
        active_path == INTEGRATION_WORKFLOW.as_posix(), "unexpected active workflow"
    )
    require(
        HEX_SHA256.fullmatch(str(active.get("sha256", ""))) is not None,
        "active workflow digest must be SHA-256",
    )
    require(
        active.get("permissions") == {"contents": "read"}, "active permissions drift"
    )
    for key in ("secrets", "variables", "environments"):
        require(active.get(key) == [], f"active workflow {key} must be empty")
    active_actions = require_sorted_unique_strings(
        active.get("actionReferences"), "active workflow actionReferences"
    )
    expected_actions = sorted(
        f"{repository}@{commit}"
        for repository, commit in manifest.get("workflowActions", {}).items()
    )
    require(
        active_actions == expected_actions, "active workflow action inventory drift"
    )

    remote = firewall.get("remote")
    require(isinstance(remote, dict), "workflowFirewall.remote is missing")
    require(remote.get("repository") == "axiomlayer/github-cli", "bad fork repository")
    require(remote.get("parent") == "cli/cli", "bad fork parent")
    require(remote.get("source") == "cli/cli", "bad fork source")
    require(remote.get("defaultBranch") == "trunk", "bad fork default branch")
    require(
        remote.get("prepublicationIndexedWorkflowPaths") == [],
        "prepublication index must record the empty default-branch index",
    )
    require(
        remote.get("strictIndexedWorkflowPaths") == [active_path],
        "strict workflow index must contain only the AxiomLayer workflow",
    )

    archive = firewall.get("archive")
    require(isinstance(archive, list) and archive, "workflow archive is missing")
    source_root = provenance["sourceRoot"]
    archive_root = provenance["archiveRoot"]
    source_paths: set[str] = set()
    archive_paths: set[str] = set()
    for index, entry in enumerate(archive):
        label = f"workflowFirewall.archive[{index}]"
        require(isinstance(entry, dict), f"{label} must be an object")
        source_path = require_safe_repository_path(
            entry.get("upstreamPath"), f"{label}.upstreamPath"
        )
        archive_path = require_safe_repository_path(
            entry.get("archivePath"), f"{label}.archivePath"
        )
        require(
            source_path.startswith(f"{source_root}/"),
            f"{label}.upstreamPath is outside provenance root",
        )
        relative = source_path.removeprefix(f"{source_root}/")
        require(
            archive_path == f"{archive_root}/{relative}",
            f"{label}.archivePath does not preserve the upstream path",
        )
        require(
            source_path not in source_paths, f"duplicate upstream path: {source_path}"
        )
        require(
            archive_path not in archive_paths, f"duplicate archive path: {archive_path}"
        )
        source_paths.add(source_path)
        archive_paths.add(archive_path)
        require(
            HEX_SHA256.fullmatch(str(entry.get("sha256", ""))) is not None,
            f"{label}.sha256 must be SHA-256",
        )
        pure = PurePosixPath(source_path)
        expected_kind = (
            "workflow"
            if pure.parent.as_posix() == source_root
            and pure.suffix in WORKFLOW_SUFFIXES
            else "support"
        )
        require(entry.get("kind") == expected_kind, f"{label}.kind is wrong")
        for key in ("secrets", "variables", "environments", "actionReferences"):
            require_sorted_unique_strings(entry.get(key), f"{label}.{key}")


def validate_manifest(manifest: dict[str, Any]) -> None:
    require(
        manifest.get("schema") == "axiomlayer-github-cli-integration-v2",
        "unexpected manifest schema",
    )
    require(manifest.get("revision") == 2, "unexpected manifest revision")

    promotion = manifest.get("promotion")
    require(isinstance(promotion, dict), "promotion must be an object")
    for field, expected in EXPECTED_PROMOTION.items():
        require(
            promotion.get(field) == expected,
            f"promotion.{field} diverges from Dotfiles #49",
        )
    for field in ("authorityCommit", "commit", "tree"):
        require(
            HEX_COMMIT.fullmatch(str(promotion[field])) is not None,
            f"promotion.{field} must be a full Git commit",
        )
    for field in ("authorityPolicySha256", "authorityVerifierSha256"):
        require(
            HEX_SHA256.fullmatch(str(promotion[field])) is not None,
            f"promotion.{field} must be SHA-256",
        )

    checksums = manifest.get("releaseChecksums")
    require(isinstance(checksums, dict), "releaseChecksums must be an object")
    require(
        checksums.get("archiveName") == "gh_2.100.0_checksums.txt",
        "release checksum asset name diverges",
    )
    require(checksums.get("archiveBytes") == 1971, "release checksum size diverges")
    require(
        checksums.get("archiveUrl")
        == f"{promotion['releaseOrigin']}/gh_2.100.0_checksums.txt",
        "release checksum URL diverges",
    )
    require(
        checksums.get("archiveSha256")
        == "6b5916dffcfa6f593b1db7890f2ddc485318e99fa263acf73aa28ebb877b53cd",
        "release checksum digest diverges",
    )

    actions = manifest.get("workflowActions")
    require(actions == EXPECTED_WORKFLOW_ACTIONS, "workflow action pins diverge")
    for action, commit in actions.items():
        require("/" in action, f"invalid action name: {action}")
        require(
            HEX_COMMIT.fullmatch(str(commit)) is not None,
            f"{action} is not pinned to a full commit",
        )

    validate_workflow_firewall_manifest(manifest)

    nix = manifest.get("nix")
    require(isinstance(nix, dict), "nix must be an object")
    require(nix.get("version") == "2.35.2", "Nix version diverges from authority")
    require(
        nix.get("installerUrl") == "https://releases.nixos.org/nix/nix-2.35.2/install",
        "Nix installer URL diverges from authority",
    )
    require(
        nix.get("installerSha256")
        == "9adda97297d9e8ab360df95c729eabff4f4f93d6db091953c3a68f29e3fb130c",
        "Nix installer digest diverges from authority",
    )
    require(
        nix.get("wrapperPath") == "integration/axiomlayer/install-nix-ci.sh",
        "Nix wrapper path diverges from authority",
    )
    require(
        nix.get("wrapperSha256") == EXPECTED_NIX_WRAPPER_SHA256,
        "Nix wrapper digest diverges from authority",
    )
    require(
        nix.get("candidateWrapperPath") == "integration/axiomlayer/run-candidate-ci.sh",
        "candidate wrapper path diverges from authority",
    )
    require(
        nix.get("candidateWrapperSha256") == EXPECTED_CANDIDATE_WRAPPER_SHA256,
        "candidate wrapper digest diverges from authority",
    )
    require(
        nix.get("derivationPath") == "integration/axiomlayer/default.nix",
        "Nix derivation path diverges from authority",
    )
    require(
        nix.get("derivationSha256") == EXPECTED_DERIVATION_SHA256,
        "Nix derivation digest diverges from authority",
    )
    require(
        nix.get("binaryTarballSha256")
        == {
            "aarch64-darwin": (
                "1695c13aba5afa7c2ecd6dc4a9393f602e7bbc440ed45e81602c831546580ec3"
            ),
            "x86_64-darwin": (
                "d725518d89f3b0b8d4af702a9d38d519814014cbe125afb3ed0545c9d755f6a5"
            ),
            "aarch64-linux": (
                "4d0302a2910f5eec1c33b8deef634f04899a75737e7001ec49908d003ae5efda"
            ),
            "x86_64-linux": (
                "0c3960a9792331a22081c3c7a5d8465db9b17c50b3acdf18587fa4c6f2cb1158"
            ),
        },
        "Nix native tarball digests diverge from authority",
    )
    require(set(nix.get("systems", [])) == EXPECTED_NIX_SYSTEMS, "bad Nix systems")
    nixpkgs = nix.get("nixpkgs")
    require(isinstance(nixpkgs, dict), "nixpkgs must be an object")
    require(
        nixpkgs.get("repository") == "axiomlayer/nixpkgs",
        "nixpkgs repository diverges from Dotfiles #49",
    )
    require(
        nixpkgs.get("url") == "https://github.com/NixOS/nixpkgs/archive/"
        "c3eea5b2156db11c7eeeada3dc737711255b253e.tar.gz",
        "nixpkgs source URL diverges from Dotfiles #49",
    )
    require(
        nixpkgs.get("commit") == "c3eea5b2156db11c7eeeada3dc737711255b253e",
        "nixpkgs commit diverges from Dotfiles #49",
    )
    require(
        nixpkgs.get("narHash") == "sha256-vdhpDJ3Lr24lkZ+fDCjmBRRtw9/vcSzkqGJOJyF5h2U=",
        "nixpkgs NAR hash diverges from Dotfiles #49",
    )
    require(
        nixpkgs.get("ghVendorHash")
        == "sha256-ZqUs2BnasF3QBX0I2Sxh2A/CnO61Vy6gRn1hkf0n9AY=",
        "GitHub CLI vendor hash diverges from pinned Nixpkgs",
    )

    credentials = manifest.get("credentials")
    require(isinstance(credentials, dict), "credentials must be an object")
    require(credentials.get("required") == [], "real credentials are forbidden")
    require(
        credentials.get("fabricated") == [], "unused fake credentials are forbidden"
    )

    targets = manifest.get("targets")
    require(isinstance(targets, dict), "targets must be an object")
    require(
        set(targets) == set(EXPECTED_TARGET_SPECS),
        "native target coverage is incomplete",
    )
    seen_runners: set[str] = set()
    for target, spec in targets.items():
        require(isinstance(spec, dict), f"{target}: target must be an object")
        expected_spec = EXPECTED_TARGET_SPECS[target]
        for field, expected in expected_spec.items():
            require(spec.get(field) == expected, f"{target}: {field} pin diverges")
        runner = spec.get("runner")
        require(isinstance(runner, str) and runner, f"{target}: runner is missing")
        require(runner not in seen_runners, f"{target}: runner label is duplicated")
        seen_runners.add(runner)
        require(
            isinstance(spec.get("archiveBytes"), int) and spec["archiveBytes"] > 0,
            f"{target}: archiveBytes must be positive",
        )
        for field in ("archiveSha256", "binarySha256"):
            require(
                HEX_SHA256.fullmatch(str(spec.get(field, ""))) is not None,
                f"{target}: {field} must be SHA-256",
            )
        require(
            spec.get("archiveUrl")
            == f"{promotion['releaseOrigin']}/{spec.get('archiveName')}",
            f"{target}: release URL is not canonical",
        )
        require(
            spec.get("promotionUrl")
            == f"{promotion['deliveryOrigin']}/{spec.get('archiveName')}",
            f"{target}: promotion URL is not canonical",
        )
        require(spec.get("binary") in {"bin/gh", "bin/gh.exe"}, f"{target}: bad binary")
        if target.startswith("windows-"):
            require(spec.get("buildMode") == "go-native", f"{target}: bad build mode")
            require(spec.get("nixSystem") is None, f"{target}: Nix must be unsupported")
            require(spec.get("format") == "zip", f"{target}: bad archive format")
            require(spec.get("archiveRoot") == "", f"{target}: unexpected archive root")
            require(spec.get("binary") == "bin/gh.exe", f"{target}: bad binary name")
        else:
            require(spec.get("buildMode") == "nix-native", f"{target}: bad build mode")
            require(
                spec.get("nixSystem") in EXPECTED_NIX_SYSTEMS,
                f"{target}: missing Nix system",
            )
            require(spec.get("archiveRoot"), f"{target}: archive root is missing")
            require(spec.get("binary") == "bin/gh", f"{target}: bad binary name")

    unsupported = manifest.get("unsupportedHostedProof")
    require(isinstance(unsupported, list), "unsupported proof must be a list")
    capabilities = {
        proof.get("capability") for proof in unsupported if isinstance(proof, dict)
    }
    require(
        {"native-nix", "native-wsl", "promotion-origin"} <= capabilities,
        "Windows Nix, WSL, and promotion-origin boundaries must be explicit",
    )


def verify_source(manifest: dict[str, Any], root: Path = REPOSITORY_ROOT) -> None:
    validate_manifest(manifest)
    promotion = manifest["promotion"]
    commit = promotion["commit"]
    tag_commit = run_git("rev-parse", f"{promotion['tag']}^{{commit}}", root=root)
    require(tag_commit == commit, f"tag resolves to {tag_commit}, not {commit}")
    tree = run_git("rev-parse", f"{commit}^{{tree}}", root=root)
    require(tree == promotion["tree"], f"source tree resolves to {tree}, not the pin")
    run_git("cat-file", "-e", f"{commit}^{{commit}}", root=root)
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    require(ancestry.returncode == 0, "promoted commit is not in the fork branch")

    go_mod = run_git("show", f"{commit}:go.mod", root=root)
    require(
        go_mod.startswith("module github.com/cli/cli/v2\n"),
        "pinned source has an unexpected module path",
    )
    require(
        re.search(r"^go 1\.26\.0$", go_mod, re.MULTILINE) is not None,
        "pinned source does not declare Go 1.26.0",
    )
    require(
        re.search(r"^toolchain go1\.26\.8$", go_mod, re.MULTILINE) is not None,
        "pinned source does not declare Go toolchain 1.26.8",
    )
    release = run_git("show", f"{commit}:.goreleaser.yml", root=root)
    for os_name in ("darwin", "linux", "windows"):
        require(f"goos: [{os_name}]" in release, f"release omits {os_name}")
    require(release.count("arm64") >= 3, "release omits an ARM64 platform")
    require(release.count("amd64") >= 3, "release omits an x86_64 platform")


def workflow_files(root: Path = REPOSITORY_ROOT) -> list[Path]:
    workflow_root = root / ".github" / "workflows"
    return sorted((*workflow_root.glob("*.yml"), *workflow_root.glob("*.yaml")))


def action_references(text: str) -> list[str]:
    return sorted({match.group(1) for match in REMOTE_USE.finditer(text)})


def environment_references(text: str) -> list[str]:
    result: set[str] = set()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)environment:\s*(.*?)\s*$", line)
        if match is None:
            continue
        indentation, raw_value = match.groups()
        value = raw_value.split(" #", 1)[0].strip().strip("\"'")
        if value:
            result.add(value)
            continue
        for child in lines[index + 1 :]:
            if not child.strip():
                continue
            child_indentation = len(child) - len(child.lstrip())
            if child_indentation <= len(indentation):
                break
            name = re.match(r"^\s*name:\s*(.*?)\s*$", child)
            if name is not None:
                child_value = name.group(1).split(" #", 1)[0].strip().strip("\"'")
                if child_value:
                    result.add(child_value)
                break
    return sorted(result)


def reference_inventory(text: str) -> dict[str, list[str]]:
    contexts: dict[str, list[str]] = {}
    for label, pattern in CONTEXT_REFERENCE.items():
        contexts[label] = sorted(
            {
                dot_reference or bracket_reference
                for dot_reference, bracket_reference in pattern.findall(text)
            }
        )
    return {
        "secrets": contexts["secrets"],
        "variables": contexts["variables"],
        "environments": environment_references(text),
        "actionReferences": action_references(text),
    }


def verify_action_pins(
    root: Path = REPOSITORY_ROOT, files: list[Path] | None = None
) -> None:
    files = workflow_files(root) if files is None else files
    require(files, "no workflow files found")
    violations: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for match in REMOTE_USE.finditer(text):
            value = match.group(1)
            line = text.count("\n", 0, match.start()) + 1
            if value.startswith("./"):
                continue
            if value.startswith("docker://"):
                if re.search(r"@sha256:[0-9a-f]{64}$", value) is None:
                    violations.append(f"{path.relative_to(root)}:{line}: {value}")
                continue
            if "@" not in value:
                violations.append(f"{path.relative_to(root)}:{line}: {value}")
                continue
            _, reference = value.rsplit("@", 1)
            if HEX_COMMIT.fullmatch(reference) is None:
                violations.append(f"{path.relative_to(root)}:{line}: {value}")
    require(
        not violations,
        "workflow actions are not pinned to immutable commits:\n"
        + "\n".join(violations),
    )


def read_git_blob(root: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise VerificationError(f"cannot read {commit}:{path}: {detail}")
    return result.stdout


def verify_workflow_archive(
    manifest: dict[str, Any],
    root: Path = REPOSITORY_ROOT,
    provenance_root: Path | None = None,
) -> None:
    provenance_root = root if provenance_root is None else provenance_root
    firewall = manifest["workflowFirewall"]
    provenance = firewall["provenance"]
    commit = provenance["commit"]
    source_root = provenance["sourceRoot"]
    archive_root = root / provenance["archiveRoot"]
    require(
        archive_root.is_dir() and not archive_root.is_symlink(),
        f"workflow archive is missing or linked: {archive_root}",
    )
    run_git("cat-file", "-e", f"{commit}^{{commit}}", root=provenance_root)
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=provenance_root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    require(ancestry.returncode == 0, "workflow provenance is not in fork history")

    tree_modes: dict[str, str] = {}
    for record in run_git(
        "ls-tree", "-r", commit, "--", source_root, root=provenance_root
    ).splitlines():
        metadata, source_path = record.split("\t", 1)
        mode, object_type, _object_id = metadata.split()
        require(
            object_type == "blob", f"workflow provenance is not a blob: {source_path}"
        )
        tree_modes[source_path] = mode
    listed_source_paths = sorted(tree_modes)
    entries = firewall["archive"]
    manifest_source_paths = sorted(entry["upstreamPath"] for entry in entries)
    require(
        manifest_source_paths == listed_source_paths,
        "upstream workflow provenance paths do not match the manifest",
    )

    actual_archive_paths: list[str] = []
    for path in archive_root.rglob("*"):
        require(not path.is_symlink(), f"workflow archive contains a symlink: {path}")
        if path.is_file():
            actual_archive_paths.append(path.relative_to(root).as_posix())
    expected_archive_paths = sorted(entry["archivePath"] for entry in entries)
    require(
        sorted(actual_archive_paths) == expected_archive_paths,
        "workflow archive contains added, missing, or renamed files",
    )

    for entry in entries:
        source_path = entry["upstreamPath"]
        expected_mode = 0o755 if tree_modes[source_path] == "100755" else 0o644
        archive_path = require_regular_repository_file(
            root, entry["archivePath"], f"workflow archive {source_path}", expected_mode
        )
        archived = archive_path.read_bytes()
        upstream = read_git_blob(provenance_root, commit, source_path)
        digest = hashlib.sha256(archived).hexdigest()
        require(digest == entry["sha256"], f"archive digest drift: {source_path}")
        require(archived == upstream, f"archive is not byte-identical: {source_path}")
        inventory = reference_inventory(archived.decode("utf-8"))
        for key, actual in inventory.items():
            require(actual == entry[key], f"{source_path}: {key} inventory drift")


def checkout_step_blocks(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if re.search(r"\buses:\s*['\"]?actions/checkout@", line) is None:
            continue
        use_indentation = len(line) - len(line.lstrip())
        block = [line]
        for following in lines[index + 1 :]:
            stripped = following.lstrip()
            indentation = len(following) - len(stripped)
            if stripped.startswith("- ") and indentation < use_indentation:
                break
            if stripped and indentation < max(0, use_indentation - 2):
                break
            block.append(following)
        blocks.append((use_indentation, "\n".join(block)))
    return blocks


def verify_active_workflow(
    manifest: dict[str, Any], root: Path = REPOSITORY_ROOT
) -> None:
    firewall = manifest["workflowFirewall"]
    active = firewall["activeWorkflow"]
    path = require_regular_repository_file(
        root, active["path"], "active workflow", 0o644
    )

    workflow_root = root / ".github" / "workflows"
    require(
        workflow_root.is_dir() and not workflow_root.is_symlink(),
        ".github/workflows must be a real directory",
    )
    all_files = sorted(
        candidate.relative_to(root).as_posix()
        for candidate in workflow_root.rglob("*")
        if candidate.is_file() or candidate.is_symlink()
    )
    require(
        all_files == [active["path"]],
        "the AxiomLayer integration must be the only file in .github/workflows",
    )

    text = path.read_text(encoding="utf-8")
    inventory = reference_inventory(text)
    for key, actual in inventory.items():
        require(actual == active[key], f"active workflow {key} inventory drift")
    for context in ("secrets", "vars"):
        require(
            re.search(rf"(?<![-A-Za-z0-9_]){context}\s*\[", text) is None,
            f"active workflow contains bracketed {context} context access",
        )
    require(
        re.search(
            r"(?i)(?<![-A-Za-z0-9_])github\s*(?:\.\s*token|\[\s*['\"]token['\"]\s*\])",
            text,
        )
        is None,
        "active workflow references github.token",
    )
    require(
        len(re.findall(r"(?m)^permissions:\s*$", text)) == 1,
        "active workflow must declare one top-level permissions block",
    )
    require(
        re.search(r"(?m)^permissions:\n  contents: read\s*$", text) is not None,
        "active workflow is not contents-read-only",
    )
    job_conditions = re.findall(r"(?m)^    if:\s*(.*?)\s*$", text)
    require(
        job_conditions
        == [TRUSTED_WORKFLOW_CONDITION, TRUSTED_WORKFLOW_CONDITION, "always()"],
        "active workflow lost an exact source job guard or unconditional terminal gate",
    )
    for authority_line in (
        'test "$REPOSITORY" = "axiomlayer/github-cli"',
        'test "$DEFAULT_BRANCH" = "trunk"',
        'test "$HEAD_REPOSITORY" = "axiomlayer/github-cli"',
        'test "$BASE_REF" = "trunk"',
        'test "$REF" = "refs/pull/$PR_NUMBER/merge"',
        'test "$WORKFLOW_REF" = "axiomlayer/github-cli/.github/workflows/axiomlayer-integration.yml@refs/pull/$PR_NUMBER/merge"',
        'test "$REF" = "refs/heads/trunk"',
        'test "$REF_PROTECTED" = "true"',
        'test "$WORKFLOW_REF" = "axiomlayer/github-cli/.github/workflows/axiomlayer-integration.yml@refs/heads/trunk"',
        'test "$CONTRACT_RESULT" = success',
        'test "$NATIVE_RESULT" = success',
    ):
        require(
            text.count(authority_line) == 1,
            f"active workflow terminal authority drifted: {authority_line}",
        )
    require("AxiomLayer/" not in text, "active workflow uses noncanonical owner casing")
    job_runners = re.findall(r"(?m)^    runs-on:\s*(.*?)\s*$", text)
    require(
        job_runners == EXPECTED_JOB_RUNNERS,
        "active workflow changed its exact hosted job runners",
    )
    matrix_runners = re.findall(r"(?m)^            runner:\s*(.*?)\s*$", text)
    require(
        matrix_runners == EXPECTED_MATRIX_RUNNERS,
        "active workflow changed its exact hosted runner matrix",
    )
    require("self-hosted" not in text, "active workflow selects a self-hosted runner")
    for device in (
        "caracal",
        "cheetah",
        "margay",
        "ocelot",
        "siberian",
        "sandcat",
        "bobcat",
    ):
        require(
            device not in text.lower(),
            f"active workflow contains device lane: {device}",
        )
    require("-latest" not in text, "active workflow uses a floating runner image")
    require(
        re.search(r"(?m)^\s+[A-Za-z-]+:\s*write\s*$", text) is None,
        "active workflow requests write authority",
    )
    for forbidden_key in ("id-token", "packages", "deployments", "attestations"):
        require(
            re.search(rf"(?m)^\s+{forbidden_key}:\s*", text) is None,
            f"active workflow requests {forbidden_key} authority",
        )
    require(
        re.search(r"(?m)^\s*environment:\s*", text) is None,
        "active workflow references a deployment environment",
    )
    require("pull_request_target" not in text, "pull_request_target is forbidden")
    require(
        "continue-on-error:" not in text,
        "active workflow permits a failed authority step to continue",
    )
    for publishing_command in (
        "gh release ",
        "git push",
        "npm publish",
        "cargo publish",
        "twine upload",
        "docker push",
        "nix copy",
        "actions/upload-artifact@",
        "actions/attest@",
    ):
        require(
            publishing_command not in text,
            f"active workflow contains publishing surface: {publishing_command}",
        )
    verify_action_pins(root, [path])
    checkout_blocks = checkout_step_blocks(text)
    require(checkout_blocks, "active workflow does not check out the source")
    for indentation, block in checkout_blocks:
        require(
            len(re.findall(rf"(?m)^ {{{indentation}}}with:\s*$", block)) == 1,
            "actions/checkout must declare a with mapping",
        )
        persisted = re.findall(r"(?m)^\s*persist-credentials:\s*(.*?)\s*$", block)
        require(
            persisted == ["false"],
            "actions/checkout must disable persisted credentials",
        )
        require(
            re.search(
                rf"(?m)^ {{{indentation + 2}}}persist-credentials:\s*false\s*$",
                block,
            )
            is not None,
            "actions/checkout persist-credentials is outside its with mapping",
        )
        clean = re.findall(r"(?m)^\s*clean:\s*(.*?)\s*$", block)
        require(clean == ["true"], "actions/checkout must request a clean checkout")
        fetch_depth = re.findall(r"(?m)^\s*fetch-depth:\s*(.*?)\s*$", block)
        require(fetch_depth == ["0"], "actions/checkout must fetch promoted history")
        checkout_ref = re.findall(r"(?m)^\s*ref:\s*(.*?)\s*$", block)
        require(
            checkout_ref == ["${{ github.sha }}"],
            "actions/checkout must use the exact event SHA",
        )
    for isolation in (
        "bash integration/axiomlayer/run-candidate-ci.sh",
        '"${{ matrix.target }}"',
        '"${{ matrix.build_mode }}"',
        '"${{ matrix.nix_system }}"',
        "remote_mode=strict-postpublication",
        'if [[ "${{ github.event_name }}" == pull_request ]]; then',
        "remote_mode=prepublication",
        '--remote-mode "$remote_mode"',
    ):
        require(isolation in text, f"native test isolation is missing: {isolation}")
    require(
        text.count("verify.py verify-nix-bootstrap") == 1,
        "active workflow must prove the Nix bootstrap exactly once",
    )
    require(
        text.count("run: sh integration/axiomlayer/install-nix-ci.sh") == 1,
        "active workflow must use the reviewed Nix wrapper exactly once",
    )
    require(
        "cachix/install-nix-action@" not in text,
        "active workflow must not delegate Nix bootstrap to an Action",
    )
    verify_nix_bootstrap(manifest, root)
    require(
        active["sha256"] == EXPECTED_ACTIVE_WORKFLOW_SHA256,
        "active workflow manifest digest can no longer be re-blessed",
    )
    assert_file_digest(path, EXPECTED_ACTIVE_WORKFLOW_SHA256, "active workflow")


def verify_nix_bootstrap(
    manifest: dict[str, Any], root: Path = REPOSITORY_ROOT
) -> None:
    nix = manifest["nix"]
    path = require_regular_repository_file(
        root, nix["wrapperPath"], "Nix wrapper", 0o755
    )
    text = path.read_text(encoding="utf-8")
    for required in (
        f"NIX_VERSION={nix['version']}",
        f"INSTALLER_URL={nix['installerUrl']}",
        f"INSTALLER_SHA256={nix['installerSha256']}",
        "env -i",
        'PATH="$SYSTEM_PATH"',
        "curl --disable",
        "--no-channel-add --no-modify-profile",
    ):
        require(required in text, f"Nix wrapper lost boundary: {required}")
    for digest in nix["binaryTarballSha256"].values():
        require(digest in text, "Nix wrapper lost a native tarball digest")
    for forbidden in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
        "github.token",
        "github_access_token",
    ):
        require(
            forbidden not in text,
            f"Nix wrapper references a live credential surface: {forbidden}",
        )
    assert_file_digest(path, EXPECTED_NIX_WRAPPER_SHA256, "Nix wrapper")
    verify_candidate_wrapper(manifest, root)


def verify_candidate_wrapper(
    manifest: dict[str, Any], root: Path = REPOSITORY_ROOT
) -> None:
    nix = manifest["nix"]
    path = require_regular_repository_file(
        root, nix["candidateWrapperPath"], "candidate wrapper", 0o755
    )
    text = path.read_text(encoding="utf-8")
    for required in (
        "env -i",
        "GOTOOLCHAIN=local",
        "GOPROXY=https://proxy.golang.org",
        '"$go_binary" test ./...',
        "--option sandbox true",
        "candidate target does not match Go platform",
        "45437bc7eeeb3359bbfddd1742f79de7652fd3e2",
    ):
        require(required in text, f"candidate wrapper lost boundary: {required}")
    require(
        text.count("env -i") == 2,
        "candidate wrapper must isolate both Go and Nix execution",
    )
    for forbidden in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
        "github.token",
        "github_access_token",
    ):
        require(
            forbidden not in text,
            f"candidate wrapper references a live credential surface: {forbidden}",
        )
    assert_file_digest(path, EXPECTED_CANDIDATE_WRAPPER_SHA256, "candidate wrapper")

    derivation = require_regular_repository_file(
        root, nix["derivationPath"], "Nix derivation", 0o644
    )
    assert_file_digest(derivation, EXPECTED_DERIVATION_SHA256, "Nix derivation")


def github_api_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "axiomlayer-workflow-firewall/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise VerificationError(
            f"GitHub API request failed for {url}: {error}"
        ) from error


def load_remote_workflow_state(
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    repository = manifest["workflowFirewall"]["remote"]["repository"]
    encoded = urllib.parse.quote(repository, safe="/")
    metadata = github_api_json(f"https://api.github.com/repos/{encoded}")
    require(isinstance(metadata, dict), "GitHub repository response is not an object")
    workflows: list[dict[str, Any]] = []
    page = 1
    total = 1
    while len(workflows) < total:
        response = github_api_json(
            f"https://api.github.com/repos/{encoded}/actions/workflows"
            f"?per_page=100&page={page}"
        )
        require(isinstance(response, dict), "GitHub workflow response is not an object")
        page_workflows = response.get("workflows")
        require(isinstance(page_workflows, list), "GitHub workflow index is not a list")
        require(
            all(isinstance(item, dict) for item in page_workflows),
            "GitHub workflow index contains a non-object",
        )
        workflows.extend(page_workflows)
        total_value = response.get("total_count")
        require(isinstance(total_value, int), "GitHub workflow total_count is invalid")
        total = total_value
        if not page_workflows:
            break
        page += 1
    require(len(workflows) == total, "GitHub workflow index pagination is incomplete")
    return metadata, workflows


def verify_remote_workflow_state(
    manifest: dict[str, Any],
    metadata: dict[str, Any],
    workflows: list[dict[str, Any]],
    remote_mode: str,
) -> None:
    require(remote_mode in REMOTE_MODES, f"unknown remote mode: {remote_mode}")
    expected = manifest["workflowFirewall"]["remote"]
    require(
        metadata.get("full_name") == expected["repository"],
        "wrong fork repository",
    )
    require(metadata.get("fork") is True, "axiomlayer/github-cli is not a fork")
    parent = metadata.get("parent")
    source = metadata.get("source")
    require(isinstance(parent, dict), "fork parent metadata is missing")
    require(isinstance(source, dict), "fork source metadata is missing")
    require(
        parent.get("full_name") == expected["parent"],
        "wrong fork parent",
    )
    require(
        source.get("full_name") == expected["source"],
        "wrong fork source",
    )
    require(
        metadata.get("default_branch") == expected["defaultBranch"],
        "wrong fork default branch",
    )

    indexed: list[str] = []
    for workflow in workflows:
        path = workflow.get("path")
        state = workflow.get("state")
        require(isinstance(path, str) and path, "indexed workflow path is invalid")
        require(state == "active", f"indexed workflow is not active: {path}")
        indexed.append(path)
    require(len(indexed) == len(set(indexed)), "duplicate indexed workflow path")
    indexed.sort()
    prepublication = sorted(expected["prepublicationIndexedWorkflowPaths"])
    strict = sorted(expected["strictIndexedWorkflowPaths"])
    if remote_mode == "prepublication":
        require(
            indexed in (prepublication, strict),
            f"prepublication remote workflow index mismatch: {indexed}",
        )
    else:
        require(indexed == strict, f"strict remote workflow index mismatch: {indexed}")


def verify_local_workflow_firewall(
    manifest: dict[str, Any],
    root: Path = REPOSITORY_ROOT,
    provenance_root: Path | None = None,
) -> None:
    validate_manifest(manifest)
    verify_workflow_archive(manifest, root, provenance_root)
    verify_active_workflow(manifest, root)


def verify_workflows(
    manifest: dict[str, Any],
    remote_mode: str,
    root: Path = REPOSITORY_ROOT,
    provenance_root: Path | None = None,
) -> None:
    verify_local_workflow_firewall(manifest, root, provenance_root)
    metadata, workflows = load_remote_workflow_state(manifest)
    verify_remote_workflow_state(manifest, metadata, workflows, remote_mode)


def safe_relative_path(name: str) -> Path:
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    require(not pure.is_absolute(), f"archive member is absolute: {name}")
    require(".." not in pure.parts, f"archive member escapes destination: {name}")
    require(
        not re.match(r"^[A-Za-z]:", normalized), f"archive member has drive: {name}"
    )
    return Path(*[part for part in pure.parts if part not in ("", ".")])


def extract_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as source:
        for member in source.getmembers():
            relative = safe_relative_path(member.name)
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            require(member.isfile(), f"unsupported tar member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = source.extractfile(member)
            require(extracted is not None, f"cannot read tar member: {member.name}")
            with extracted, target.open("wb") as output:
                shutil.copyfileobj(extracted, output)
            target.chmod(member.mode & 0o777)


def extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            relative = safe_relative_path(member.filename)
            target = destination / relative
            mode = member.external_attr >> 16
            require(
                not stat.S_ISLNK(mode),
                f"symbolic links are forbidden in zip: {member.filename}",
            )
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as extracted, target.open("wb") as output:
                shutil.copyfileobj(extracted, output)
            if mode:
                target.chmod(mode & 0o777)


def extract_archive(archive: Path, archive_format: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive_format == "tar.gz":
        extract_tar(archive, destination)
    elif archive_format == "zip":
        extract_zip(archive, destination)
    else:
        raise VerificationError(f"unsupported archive format: {archive_format}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_file_digest(
    path: Path, expected: str, label: str, expected_bytes: int | None = None
) -> None:
    require(
        path.is_file() and not path.is_symlink(),
        f"{label} is missing or linked: {path}",
    )
    if expected_bytes is not None:
        require(
            path.stat().st_size == expected_bytes,
            f"{label} size is {path.stat().st_size}, expected {expected_bytes}",
        )
    actual = sha256_file(path)
    require(actual == expected, f"{label} SHA-256 is {actual}, expected {expected}")


def sterile_execution_environment(temporary: Path) -> dict[str, str]:
    environment = {
        "HOME": str(temporary / "home"),
        "PATH": os.defpath,
        "TMPDIR": str(temporary),
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "GH_TELEMETRY": "false",
        "NO_COLOR": "1",
    }
    if os.name == "nt":
        for variable in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
            value = os.environ.get(variable)
            require(value is not None, f"Windows execution requires {variable}")
            environment[variable] = value
    return environment


def download_verified(
    url: str, destination: Path, expected: str, expected_bytes: int
) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "axiomlayer-integration/1"}
    )
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with (
            urllib.request.urlopen(request, timeout=90) as response,
            temporary.open("wb") as output,
        ):
            shutil.copyfileobj(response, output, length=1024 * 1024)
        assert_file_digest(temporary, expected, "download", expected_bytes)
        temporary.replace(destination)
    except VerificationError:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError) as error:
        temporary.unlink(missing_ok=True)
        raise VerificationError(f"download failed for {url}: {error}") from error


def ensure_archive(spec: dict[str, Any], directory: Path) -> Path:
    require(not directory.is_symlink(), f"artifact directory is linked: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / str(spec["archiveName"])
    if not archive.exists():
        download_verified(
            str(spec["archiveUrl"]),
            archive,
            str(spec["archiveSha256"]),
            int(spec["archiveBytes"]),
        )
    assert_file_digest(
        archive,
        str(spec["archiveSha256"]),
        "archive",
        int(spec["archiveBytes"]),
    )
    return archive


def verify_asset(
    manifest: dict[str, Any],
    target: str,
    directory: Path,
    execute: bool = False,
    receipt: Path | None = None,
) -> dict[str, Any]:
    validate_manifest(manifest)
    require(target in manifest["targets"], f"unknown target: {target}")
    spec = manifest["targets"][target]
    archive = ensure_archive(spec, directory)
    with tempfile.TemporaryDirectory(
        prefix=f"github-cli-{target}-", dir=directory
    ) as raw:
        extraction = Path(raw)
        extract_archive(archive, str(spec["format"]), extraction)
        binary = extraction / str(spec["archiveRoot"]) / str(spec["binary"])
        assert_file_digest(binary, str(spec["binarySha256"]), "binary")
        version_output: str | None = None
        if execute:
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
            execution_home = extraction / "home"
            execution_home.mkdir()
            result = subprocess.run(
                [str(binary), "--version"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=sterile_execution_environment(extraction),
            )
            require(result.returncode == 0, f"binary failed: {result.stderr.strip()}")
            version_output = result.stdout.splitlines()[0] if result.stdout else ""
            require(
                version_output.startswith("gh version 2.100.0 "),
                f"unexpected version output: {version_output}",
            )
        evidence = {
            "schema": "axiomlayer-github-cli-receipt-v1",
            "target": target,
            "commit": manifest["promotion"]["commit"],
            "version": manifest["promotion"]["version"],
            "archive": spec["archiveName"],
            "archiveSha256": spec["archiveSha256"],
            "binarySha256": spec["binarySha256"],
            "executed": execute,
            "versionOutput": version_output,
        }
        if receipt is not None:
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        return evidence


def verify_release_checksums(manifest: dict[str, Any], directory: Path) -> None:
    validate_manifest(manifest)
    checksums = manifest["releaseChecksums"]
    checksum_file = ensure_archive(checksums, directory)
    published: dict[str, str] = {}
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        require(len(fields) == 2, f"malformed upstream checksum line: {line}")
        digest, name = fields
        require(
            HEX_SHA256.fullmatch(digest) is not None, f"bad upstream digest: {line}"
        )
        require(name not in published, f"duplicate upstream checksum: {name}")
        published[name] = digest
    for target, spec in manifest["targets"].items():
        require(
            published.get(spec["archiveName"]) == spec["archiveSha256"],
            f"{target}: device archive pin disagrees with upstream checksums",
        )


def verify_all_assets(manifest: dict[str, Any], directory: Path) -> None:
    verify_release_checksums(manifest, directory)
    print("release_checksums=verified")
    for target in sorted(manifest["targets"]):
        verify_asset(manifest, target, directory)
        print(f"artifact={target}:verified")


def materialize_source(
    manifest: dict[str, Any], destination: Path, root: Path = REPOSITORY_ROOT
) -> None:
    verify_source(manifest, root)
    if destination.exists():
        require(
            not any(destination.iterdir()), f"destination is not empty: {destination}"
        )
    destination.mkdir(parents=True, exist_ok=True)
    archive_handle, archive_name = tempfile.mkstemp(
        prefix="github-cli-source-", suffix=".tar", dir=destination.parent
    )
    os.close(archive_handle)
    archive = Path(archive_name)
    try:
        run_git(
            "archive",
            "--format=tar",
            f"--output={archive}",
            manifest["promotion"]["commit"],
            root=root,
        )
        extract_tar(archive, destination)
    finally:
        archive.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    commands.add_parser("verify-source")
    commands.add_parser("verify-nix-bootstrap")
    workflows = commands.add_parser("verify-workflows")
    workflows.add_argument("--remote-mode", choices=sorted(REMOTE_MODES), required=True)
    materialize = commands.add_parser("materialize-source")
    materialize.add_argument("--destination", type=Path, required=True)
    asset = commands.add_parser("verify-asset")
    asset.add_argument("--target", required=True)
    asset.add_argument("--directory", type=Path, required=True)
    asset.add_argument("--execute", action="store_true")
    asset.add_argument("--receipt", type=Path)
    assets = commands.add_parser("verify-all-assets")
    assets.add_argument("--directory", type=Path, required=True)
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        manifest = load_manifest(arguments.manifest)
        if arguments.command == "validate":
            validate_manifest(manifest)
            print("manifest=valid")
        elif arguments.command == "verify-source":
            verify_source(manifest)
            print(f"source={manifest['promotion']['commit']}:verified")
        elif arguments.command == "verify-nix-bootstrap":
            validate_manifest(manifest)
            verify_nix_bootstrap(manifest)
            print("nix_bootstrap=credential-sterile:verified")
        elif arguments.command == "verify-workflows":
            verify_workflows(manifest, arguments.remote_mode)
            print(f"workflows=single-read-only:{arguments.remote_mode}")
        elif arguments.command == "materialize-source":
            materialize_source(manifest, arguments.destination)
            print(f"source_materialized={arguments.destination}")
        elif arguments.command == "verify-asset":
            evidence = verify_asset(
                manifest,
                arguments.target,
                arguments.directory,
                execute=arguments.execute,
                receipt=arguments.receipt,
            )
            print(json.dumps(evidence, sort_keys=True))
        elif arguments.command == "verify-all-assets":
            verify_all_assets(manifest, arguments.directory)
        else:
            raise VerificationError(f"unknown command: {arguments.command}")
    except VerificationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
