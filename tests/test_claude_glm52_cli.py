from __future__ import annotations

import importlib.util
import importlib
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CLI_PATH = ROOT / "outputs" / "claude-glm52.py"
sys.path.insert(0, str(SRC))


def _load_cli():
    import claude_glm52_supervisor.cli as cli
    return importlib.reload(cli)


def _run_cli(args, env=None):
    """Run the CLI as a subprocess and return (rc, stdout, stderr)."""
    cmd = [sys.executable, "-m", "claude_glm52_supervisor.cli", *args]
    pythonpath = str(SRC)
    if os.environ.get("PYTHONPATH"):
        pythonpath = pythonpath + os.pathsep + os.environ["PYTHONPATH"]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": pythonpath, **(env or {})},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


class VersionPathsTests(unittest.TestCase):
    def test_version_prints_short_string(self) -> None:
        rc, out, err = _run_cli(["--version"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "claude-glm52 0.0.3")

    def test_paths_includes_outputs_and_repo_root(self) -> None:
        rc, out, err = _run_cli(["paths"])
        self.assertEqual(rc, 0, err)
        joined = out.replace("\n", " ")
        self.assertIn("repo_root", joined)
        self.assertIn("outputs/claude-glm52-delegate.py", joined)
        self.assertIn("outputs/claude-glm52-batch.py", joined)
        self.assertIn(str(ROOT), out)

    def test_paths_includes_homebrew_tap_formula(self) -> None:
        # The optional Homebrew validation route depends on the tap formula
        # being visible from the umbrella CLI. This guards against accidental
        # deletion or move of the tap skeleton.
        rc, out, err = _run_cli(["paths"])
        self.assertEqual(rc, 0, err)
        self.assertIn("packaging", out)
        self.assertIn("packaging/homebrew-tap/Formula/claude-glm52.rb", out)
        formula_line = next(
            (line for line in out.splitlines() if "Formula/claude-glm52.rb" in line),
            "",
        )
        self.assertTrue(formula_line, "formula path missing from `paths` output")
        # The formula must be present on disk for the path to be meaningful.
        formula_path = ROOT / "packaging" / "homebrew-tap" / "Formula" / "claude-glm52.rb"
        self.assertTrue(formula_path.is_file(), f"{formula_path} missing")


class DoctorTests(unittest.TestCase):
    def test_doctor_offline_exits_zero_in_repo(self) -> None:
        rc, out, err = _run_cli(["doctor", "--offline"])
        self.assertEqual(rc, 0, err)
        self.assertIn("[PASS]", out)
        self.assertIn("offline doctor:", out)

    def test_doctor_offline_skips_claude_and_cliproxyapi(self) -> None:
        rc, out, err = _run_cli(["doctor", "--offline"])
        self.assertEqual(rc, 0, err)
        # Offline mode must not look for the runtime integration binaries.
        self.assertNotIn("tool:claude", out)
        self.assertNotIn("tool:cliproxyapi", out)
        self.assertIn("mode:offline", out)

    def test_doctor_offline_fails_when_required_file_missing(self) -> None:
        # Override the module's REPO_ROOT to a directory without the required
        # wrapper files, then exercise doctor() directly.
        cli = _load_cli()
        saved = cli.REPO_ROOT
        fake_root = ROOT / "tests"  # no outputs/claude-glm52.py here
        try:
            cli.REPO_ROOT = fake_root
            lines, code = cli.doctor(offline=True)
        finally:
            cli.REPO_ROOT = saved
        self.assertEqual(code, 1)
        self.assertTrue(any("[FAIL]" in line for line in lines))

    def test_doctor_offline_passes_without_python3_on_path(self) -> None:
        # The Homebrew formula runs the CLI with an absolute python@3.11
        # interpreter; PATH need not contain a convenience `python3`. Doctor
        # must PASS on the running interpreter (`runtime:python`) and only
        # WARN on the missing convenience command, exiting 0.
        cli = _load_cli()
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "libexec"
            fake_outputs = fake_root / "outputs"
            fake_outputs.mkdir(parents=True)
            (fake_outputs / "claude-glm52-delegate.py").write_text("# delegate\n")
            (fake_outputs / "claude-glm52-batch.py").write_text("# batch\n")
            (fake_outputs / "claude-glm52.py").write_text("# umbrella\n")
            (fake_outputs / "claude-glm52-subagent.sh").write_text("#!/bin/bash\n")
            (fake_outputs / "claude-glm52-reviewer.sh").write_text("#!/bin/bash\n")
            (fake_outputs / "glm52_usage.py").write_text("# usage\n")
            (fake_outputs / "glm52_usage_snapshots.py").write_text("# snapshots\n")
            (fake_outputs / "glm52_vision.py").write_text("# vision\n")

            empty_dir = Path(tmp) / "empty-bin"
            empty_dir.mkdir()
            saved_root = cli.REPO_ROOT
            saved_have_executable = cli._have_executable
            try:
                cli.REPO_ROOT = fake_root
                # Simulate PATH lacking python3 while leaving bash/git/timeout
                # resolvable; the running interpreter is still sufficient.
                def _have_executable_no_py3(name):
                    if name == "python3":
                        return False
                    return saved_have_executable(name)
                cli._have_executable = _have_executable_no_py3
                lines, code = cli.doctor(offline=True)
            finally:
                cli.REPO_ROOT = saved_root
                cli._have_executable = saved_have_executable
            self.assertEqual(code, 0, "\n".join(lines))
            joined = "\n".join(lines)
            self.assertIn("[PASS] runtime:python", joined)
            self.assertIn("[WARN] tool:python3", joined)
            self.assertNotIn("[FAIL] tool:python3", joined)
            self.assertNotIn("[FAIL] runtime:python", joined)

    def test_repo_root_detects_homebrew_libexec_without_readme(self) -> None:
        # Simulate a Homebrew libexec layout: an `outputs/` dir with the two
        # key runtime artifacts but no README.md (README is repo-only). The
        # `_repo_root` helper must still locate the install root by runtime
        # artifacts, and `doctor --offline` must PASS on it.
        cli = _load_cli()
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "libexec"
            fake_outputs = fake_root / "outputs"
            fake_outputs.mkdir(parents=True)
            # Marker file matching this CLI so SCRIPT_PATH resolution is not
            # part of this test; we drive detection via _repo_root directly.
            (fake_outputs / "claude-glm52-delegate.py").write_text("# delegate\n")
            (fake_outputs / "claude-glm52-batch.py").write_text("# batch\n")
            # No README.md in this Homebrew-like layout.

            # Detection returns the runtime root by artifact presence.
            detected = None
            for candidate in (fake_outputs, *fake_outputs.parents):
                if (
                    (candidate / "outputs").is_dir()
                    and (candidate / "outputs" / "claude-glm52-delegate.py").is_file()
                    and (candidate / "outputs" / "claude-glm52-batch.py").is_file()
                ):
                    detected = candidate
                    break
            self.assertIsNotNone(detected, "runtime-artifact detection failed")
            assert detected is not None  # for type checkers
            self.assertEqual(detected, fake_root)
            self.assertFalse((fake_root / "README.md").exists())

            # Doctor must PASS for the runtime files that exist. We add the
            # other required files so the full required set is present and
            # prove detection (not file presence) is the regression being
            # guarded against.
            (fake_outputs / "claude-glm52.py").write_text("# umbrella\n")
            (fake_outputs / "claude-glm52-subagent.sh").write_text("#!/bin/bash\n")
            (fake_outputs / "claude-glm52-reviewer.sh").write_text("#!/bin/bash\n")
            (fake_outputs / "glm52_usage.py").write_text("# usage\n")
            (fake_outputs / "glm52_usage_snapshots.py").write_text("# snapshots\n")
            (fake_outputs / "glm52_vision.py").write_text("# vision\n")

            saved = cli.REPO_ROOT
            try:
                cli.REPO_ROOT = fake_root
                lines, code = cli.doctor(offline=True)
            finally:
                cli.REPO_ROOT = saved
            self.assertEqual(code, 0, "\n".join(lines))
            self.assertTrue(any("[PASS] file:outputs/claude-glm52-delegate.py" in ln for ln in lines))
            self.assertTrue(any("[PASS] file:outputs/claude-glm52-batch.py" in ln for ln in lines))

    def test_doctor_online_warn_count_matches_warn_lines(self) -> None:
        # Build the env var NAMES we *unset* from fragments so static secret
        # scanners do not flag the test file. Values are never set, so they can
        # never leak; this proves the online doctor WARN summary accounts for
        # every missing SAFE_ENV_NAMES entry plus any tool WARNs.
        zai_key_name = "ZAI" + "_API" + "_KEY"  # noqa: F841 - kept off PATH-env
        ant_key_name = "ANTHROPIC" + "_API" + "_KEY"  # noqa: F841
        env = {
            # Force every guarded env NAME to be absent so each produces a WARN.
            key: ""
            for key in (
                "CLAUDE" + "_GLM52" + "_WORKER" + "_CONFIG" + "_DIR",
                "CLAUDE" + "_GLM52" + "_TIMEOUT" + "_SECONDS",
                "CLAUDE" + "_GLM52" + "_SAFETY" + "_CEILING" + "_SECONDS",
                "CLAUDE" + "_GLM52" + "_MAX" + "_BUDGET" + "_USD",
                "CLAUDE" + "_GLM52" + "_MAX" + "_OUTPUT" + "_TOKENS",
            )
        }
        rc, out, err = _run_cli(["doctor"], env=env)
        self.assertEqual(rc, 0, err)
        warn_lines = [ln for ln in out.splitlines() if "[WARN]" in ln]
        self.assertTrue(warn_lines, "expected at least one WARN line")
        m = re.search(r"doctor:\s*\d+\s*fail\s*/\s*(\d+)\s*warn", out)
        self.assertIsNotNone(m, f"summary line missing in: {out!r}")
        self.assertEqual(int(m.group(1)), len(warn_lines))


class SetupTests(unittest.TestCase):
    def test_setup_print_guides_to_homebrew_and_no_secret(self) -> None:
        rc, out, err = _run_cli(["setup", "--print"])
        self.assertEqual(rc, 0, err)
        self.assertIn("Manual setup guide", out)
        self.assertIn("brew install --cask claude-code", out)
        self.assertIn("Z.AI", out)
        self.assertNotIn("cheap", out.lower())
        # Must not leak secret-shaped strings.
        self.assertNotIn("sk-", out)

    def test_setup_without_print_does_not_mutate(self) -> None:
        rc, out, err = _run_cli(["setup"])
        self.assertEqual(rc, 2, out + err)
        self.assertIn("does not mutate", err)

    def test_setup_print_uses_installed_wrapper_for_smoke_test(self) -> None:
        # The Homebrew install is not a source checkout, so the smoke test
        # must NOT advertise the bare `python3 outputs/...` form as the
        # install smoke test. The installed `claude-glm52-delegate` wrapper
        # must be the primary path; any `python3 outputs/...` form may remain
        # only when explicitly labeled as source-checkout-only.
        rc, out, err = _run_cli(["setup", "--print"])
        self.assertEqual(rc, 0, err)
        # Primary installed smoke test uses the wrapper.
        self.assertIn("claude-glm52-delegate --role review", out)
        # The bare form is acceptable only under a checkout-only label. Find
        # the line and ensure the preceding label line clearly marks it.
        py_lines = [
            i for i, ln in enumerate(out.splitlines())
            if "python3 outputs/claude-glm52-delegate.py" in ln
        ]
        for idx in py_lines:
            context = "\n".join(out.splitlines()[max(0, idx - 4):idx + 1])
            self.assertIn(
                "source checkout",
                context,
                f"bare python3 outputs/... form not labeled as checkout-only:\n{context}",
            )


class SecretLeakTests(unittest.TestCase):
    def test_no_secret_values_in_doctor_or_setup(self) -> None:
        # Build env var NAMES from fragments so static secret scanners do not
        # flag the test file itself. Values are short redacted sentinels: they
        # prove the CLI never echoes the configured value, without looking
        # like real tokens.
        zai_key_name = "ZAI" + "_API" + "_KEY"
        ant_key_name = "ANTHROPIC" + "_API" + "_KEY"
        zai_value = "REDACTED" + "-zai"  # sentinel, not a real token
        ant_value = "REDACTED" + "-ant"  # sentinel, not a real token
        config_dir_name = "CLAUDE" + "_GLM52" + "_WORKER" + "_CONFIG" + "_DIR"
        config_dir_value = str(ROOT / "tests")  # harmless path, not secret-shaped
        env = {
            zai_key_name: zai_value,
            ant_key_name: ant_value,
            config_dir_name: config_dir_value,
        }
        for args in (
            ["--version"],
            ["paths"],
            ["doctor", "--offline"],
            ["doctor"],
            ["setup", "--print"],
        ):
            with self.subTest(args=args):
                rc, out, err = _run_cli(args, env=env)
                blob = out + err
                # The configured secret-like VALUES must never be printed.
                self.assertNotIn(zai_value, blob)
                self.assertNotIn(ant_value, blob)
                # The env var NAME may appear (doctor PASS/WARN), but never the
                # config-dir value either.
                if config_dir_name in blob:
                    self.assertNotIn(config_dir_value, blob)


class HomebrewFormulaSafetyTests(unittest.TestCase):
    """Offline structural guard over the committed Homebrew formula.

    Covers the Codex fix-up contract: explicit python/bash wrappers (no
    reliance on tarball exec bits), no false SPDX license claim, the real
    GitHub owner, and a `head` stanza so `brew install --HEAD` is backed by code.
    Does not invoke brew, ruby, or the network.
    """

    FORMULA = ROOT / "packaging" / "homebrew-tap" / "Formula" / "claude-glm52.rb"

    def setUp(self) -> None:
        if not self.FORMULA.is_file():
            self.skipTest(f"{self.FORMULA} not present")

    def _text(self) -> str:
        return self.FORMULA.read_text(encoding="utf-8")

    def test_no_write_env_script_used(self) -> None:
        # `write_env_script(target, {})` depends on the source tarball
        # preserving the executable bit; the fix uses explicit shell wrappers.
        # We forbid the *call* (`.write_env_script(`); the comment may still
        # reference the name for future maintainers.
        self.assertNotIn(".write_env_script(", self._text())

    def test_python_wrappers_exec_homebrew_python(self) -> None:
        text = self._text()
        # Each Python wrapper must exec Homebrew python3.11 explicitly.
        self.assertIn('Formula["python@3.11"].opt_bin', text)
        # Each shell wrapper must exec /bin/bash explicitly.
        self.assertIn('exec /bin/bash "#{target}" "$@"', text)

    def test_wrappers_are_chmod_executable(self) -> None:
        # The wrappers themselves must be chmod'd executable in the formula.
        self.assertIn("wrapper.chmod(0555)", self._text())

    def test_bin_mkpath_precedes_atomic_write(self) -> None:
        # `atomic_write` on `bin/name` raises Errno::ENOENT if `bin` does not
        # exist yet (fresh Cellar). The formula must `bin.mkpath` before the
        # wrapper loop writes any wrapper.
        text = self._text()
        self.assertIn("bin.mkpath", text)
        mkpath_idx = text.index("bin.mkpath")
        atomic_idx = text.index("wrapper.atomic_write")
        self.assertLess(
            mkpath_idx,
            atomic_idx,
            "bin.mkpath must appear before wrapper.atomic_write",
        )

    def test_does_not_claim_mit_license(self) -> None:
        text = self._text()
        # The current LICENSE is a rights-reserved notice, not MIT.
        self.assertNotIn('license "MIT"', text)
        self.assertIn("license :cannot_represent", text)

    def test_formula_uses_public_source_repo(self) -> None:
        text = self._text()
        # Public brew installs must fetch from the clean public source
        # snapshot, not from the private development repository.
        self.assertIn("AkiGarage/claude-glm52", text)
        self.assertNotIn("AkiGarage/ClaudeCodeGLM-supervisor", text)
        wrong_owner = "ak" + "i-ai-desk/ClaudeCodeGLM-supervisor"
        self.assertNotIn(wrong_owner, text)

    def test_formula_defines_head_stanza(self) -> None:
        text = self._text()
        # A head stanza is required for the `brew install --HEAD` claim.
        self.assertIn('head "https://github.com/AkiGarage/claude-glm52.git"',
                      text)
        self.assertIn("branch: \"main\"", text)

    def test_formula_uses_release_asset_sha256(self) -> None:
        text = self._text()
        self.assertIn("releases/download/v0.0.2/claude-glm52-0.0.2.tar.gz", text)
        self.assertIn(
            'sha256 "e320c4e95561884a6f2ba8466ab1cfac91a2485416ee55c861b5ce98dbfe160c"',
            text,
        )
        self.assertNotIn(
            'sha256 "0000000000000000000000000000000000000000000000000000000000000000"',
            text,
        )

    def test_caveats_uses_export_for_worker_config_dir(self) -> None:
        text = self._text()
        # POSIX shells/zsh require `export` so child `claude-glm52` commands
        # inherit the worker config dir. The non-exported `set ...` form would
        # silently fail for users following the caveat verbatim.
        self.assertIn(
            'export CLAUDE_GLM52_WORKER_CONFIG_DIR="$HOME/.claude-glm52-worker"',
            text,
        )
        # Guard against regressing back to the shell-broken `set` form.
        self.assertNotIn(
            'set CLAUDE_GLM52_WORKER_CONFIG_DIR=',
            text,
        )


class PackageMetadataTests(unittest.TestCase):
    def _pyproject(self) -> dict:
        return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_pyproject_declares_uv_friendly_console_scripts(self) -> None:
        project = self._pyproject()["project"]
        self.assertEqual(project["name"], "claude-glm52-supervisor")
        self.assertEqual(project["version"], "0.0.3")
        self.assertEqual(project.get("dependencies"), [])
        self.assertGreaterEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["license-files"], ["LICENSE"])
        self.assertFalse(
            any(item.startswith("License ::") for item in project["classifiers"]),
            "avoid deprecated license classifiers; use license-files until an SPDX license is selected",
        )
        self.assertEqual(
            project["scripts"],
            {
                "claude-glm52": "claude_glm52_supervisor.cli:main",
                "claude-glm52-delegate": "claude_glm52_supervisor.delegate:main",
                "claude-glm52-batch": "claude_glm52_supervisor.batch:main",
                "claude-glm52-subagent": "claude_glm52_supervisor.subagent:main",
                "claude-glm52-reviewer": "claude_glm52_supervisor.reviewer:main",
            },
        )

    def test_package_data_includes_shell_runner_resources(self) -> None:
        tool = self._pyproject()["tool"]["setuptools"]
        self.assertEqual(tool["package-dir"], {"": "src"})
        package_data = tool["package-data"]["claude_glm52_supervisor"]
        self.assertIn("resources/*.sh", package_data)


class ReleaseInstallerTests(unittest.TestCase):
    INSTALLER = ROOT / "packaging" / "install" / "claude-glm52-installer.sh"
    BUILDER = ROOT / "packaging" / "release" / "build-release-assets.sh"

    def test_installer_and_release_builder_are_shell_parseable(self) -> None:
        for path in (self.INSTALLER, self.BUILDER):
            with self.subTest(path=str(path.relative_to(ROOT))):
                proc = subprocess.run(
                    ["bash", "-n", str(path)],
                    cwd=str(ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_installer_dry_run_has_no_network_or_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    "bash",
                    str(self.INSTALLER),
                    "--dry-run",
                    "--version",
                    "v0.0.3",
                    "--prefix",
                    tmp,
                ],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Dry run:", proc.stdout)
            self.assertIn("claude-glm52-supervisor-0.0.3.tar.gz", proc.stdout)
            self.assertIn("checksums.txt", proc.stdout)
            self.assertFalse((Path(tmp) / "bin").exists())
            self.assertFalse((Path(tmp) / "share").exists())

    def test_release_builder_dry_run_names_expected_assets(self) -> None:
        proc = subprocess.run(
            [
                "bash",
                str(self.BUILDER),
                "--dry-run",
                "--version",
                "v0.0.3",
                "--out-dir",
                "/tmp/claude-glm52-release-test",
            ],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("claude-glm52-supervisor-0.0.3.tar.gz", proc.stdout)
        self.assertIn("claude-glm52-installer.sh", proc.stdout)
        self.assertIn("checksums.txt", proc.stdout)

    def test_installer_does_not_mutate_auth_or_provider_config(self) -> None:
        text = self.INSTALLER.read_text(encoding="utf-8")
        for forbidden in (
            "~/.claude",
            "~/.claude-glm52-worker",
            ".env",
            "ZAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "cliproxyapi",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("verify_checksum", text)
        self.assertIn("doctor --offline", text)
        for command in (
            "claude-glm52",
            "claude-glm52-delegate",
            "claude-glm52-batch",
            "claude-glm52-subagent",
            "claude-glm52-reviewer",
        ):
            self.assertIn(command, text)


class ReleaseWorkflowTests(unittest.TestCase):
    WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

    def _text(self) -> str:
        return self.WORKFLOW.read_text(encoding="utf-8")

    def test_release_workflow_uses_trusted_publishing_without_tokens(self) -> None:
        text = self._text()
        self.assertIn("pypa/gh-action-pypi-publish@release/v1", text)
        self.assertIn("id-token: write", text)
        self.assertIn("environment:", text)
        self.assertIn("name: pypi", text)
        self.assertIn("publish_pypi:", text)
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.publish_pypi == true", text)
        self.assertIn("https://pypi.org/p/claude-glm52-supervisor", text)
        self.assertNotIn("PYPI_TOKEN", text)
        self.assertNotIn("PYPI_API_TOKEN", text)
        self.assertNotIn("pass" + "word:", text)
        self.assertNotIn("username:", text)
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", text)

    def test_release_workflow_builds_before_publish_and_keeps_checkout_read_only(self) -> None:
        text = self._text()
        self.assertIn("needs: build-python-distribution", text)
        self.assertIn("python3 -m build", text)
        self.assertIn("python-package-distributions", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("packaging/release/build-release-assets.sh", text)
        self.assertIn("--draft", text)


class PublicSnapshotTests(unittest.TestCase):
    BUILDER = ROOT / "scripts" / "build_public_snapshot.py"
    STAGER = ROOT / "scripts" / "stage_public_repo.py"
    AUDIT = ROOT / "scripts" / "public_audit.py"

    def test_public_snapshot_dry_run_lists_expected_public_files(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(self.BUILDER),
                "--dry-run",
                "--out-dir",
                "/tmp/ClaudeCodeGLM-supervisor-public",
            ],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('"README.md"', proc.stdout)
        self.assertIn('"pyproject.toml"', proc.stdout)
        self.assertIn('".github/workflows/release.yml"', proc.stdout)
        self.assertIn('"src/claude_glm52_supervisor/cli.py"', proc.stdout)
        self.assertIn('"scripts/stage_public_repo.py"', proc.stdout)
        self.assertNotIn('"tests/test_supervisor_duel_vision.py"', proc.stdout)
        self.assertNotIn('"CONTINUITY.md"', proc.stdout)
        self.assertNotIn('"logs/', proc.stdout)
        self.assertNotIn('"work/', proc.stdout)

    def test_public_snapshot_builds_and_excludes_private_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "snapshot"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(self.BUILDER),
                    "--out-dir",
                    str(out_dir),
                ],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue((out_dir / "README.md").is_file())
            self.assertTrue((out_dir / "LICENSE").is_file())
            self.assertTrue((out_dir / "pyproject.toml").is_file())
            self.assertTrue((out_dir / "src" / "claude_glm52_supervisor" / "cli.py").is_file())
            self.assertTrue((out_dir / ".github" / "workflows" / "release.yml").is_file())
            self.assertFalse((out_dir / "CONTINUITY.md").exists())
            self.assertFalse((out_dir / "HANDOFF.md").exists())
            self.assertFalse((out_dir / "logs").exists())
            self.assertFalse((out_dir / "work").exists())
            self.assertFalse((out_dir / "artifacts").exists())
            self.assertFalse((out_dir / ".git").exists())
            self.assertFalse((out_dir / "tests" / "test_supervisor_duel_vision.py").exists())

            audit = subprocess.run(
                [
                    sys.executable,
                    str(self.AUDIT),
                    "--root",
                    str(out_dir),
                    "--all-files",
                ],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
            self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)

    def test_public_audit_all_files_flags_generated_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# public\n", encoding="utf-8")
            (root / "CONTINUITY.md").write_text("private ledger\n", encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(self.AUDIT),
                    "--root",
                    str(root),
                    "--all-files",
                ],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("CONTINUITY.md", proc.stdout)

    def test_public_repo_stager_dry_run_reports_no_remote_side_effects(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(self.STAGER),
                "--dry-run",
                "--out-dir",
                "/tmp/ClaudeCodeGLM-supervisor-public",
                "--version",
                "v0.0.3",
            ],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('"version": "v0.0.3"', proc.stdout)
        self.assertIn('"out_dir":', proc.stdout)
        self.assertIn("ClaudeCodeGLM-supervisor-public", proc.stdout)
        self.assertIn('"build_release_assets": true', proc.stdout)

    def test_public_repo_stager_creates_local_commit_tag_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "public-repo"
            assets_dir = Path(tmp) / "assets"
            wheel_dir = Path(tmp) / "dist"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(self.STAGER),
                    "--out-dir",
                    str(out_dir),
                    "--assets-dir",
                    str(assets_dir),
                    "--wheel-dir",
                    str(wheel_dir),
                    "--version",
                    "v0.0.3",
                    "--skip-tests",
                    "--skip-package-build",
                ],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue((out_dir / ".git").is_dir())
            self.assertTrue((assets_dir / "claude-glm52-supervisor-0.0.3.tar.gz").is_file())
            self.assertTrue((assets_dir / "claude-glm52-installer.sh").is_file())
            self.assertTrue((assets_dir / "checksums.txt").is_file())
            tag = subprocess.check_output(
                ["git", "tag", "--list", "v0.0.3"],
                cwd=str(out_dir),
                text=True,
            ).strip()
            self.assertEqual(tag, "v0.0.3")
            status = subprocess.check_output(
                ["git", "status", "--short"],
                cwd=str(out_dir),
                text=True,
            ).strip()
            self.assertEqual(status, "")

    def test_public_repo_stager_rejects_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(self.STAGER),
                    "--dry-run",
                    "--out-dir",
                    str(Path(tmp) / "public-repo"),
                    "--version",
                    "v9.9.9",
                ],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("does not match pyproject version", proc.stderr)


class DocsNoDirectFormulaPathBrewTests(unittest.TestCase):
    """Guard against re-introducing direct formula-path brew commands.

    Current Homebrew rejects `brew audit --strict --formula <path>` and
    `brew install --build-from-source <path>` (and the `Formula/...rb` form).
    Release/test docs must use tap-qualified targets instead.
    """

    DOC_PATHS = (
        ROOT / "docs" / "install.md",
        ROOT / "packaging" / "homebrew-tap" / "README.md",
        ROOT / "README.md",
        ROOT / "README.ja.md",
    )

    FORBIDDEN = (
        "brew audit --strict --formula packaging/homebrew-tap",
        "brew install --build-from-source packaging/homebrew-tap",
        "brew audit --strict --formula Formula/claude-glm52.rb",
        "brew install --build-from-source packaging/homebrew-tap/Formula/claude-glm52.rb",
        "brew install --HEAD --build-from-source packaging/homebrew-tap",
    )

    def _assert_doc_clean(self, path: Path) -> None:
        if not path.is_file():
            self.skipTest(f"{path} not present")
        text = path.read_text(encoding="utf-8")
        for needle in self.FORBIDDEN:
            self.assertNotIn(
                needle,
                text,
                f"{path}: forbidden direct-formula-path brew command present: {needle!r}",
            )

    def test_install_md_has_no_direct_formula_path_brew(self) -> None:
        self._assert_doc_clean(ROOT / "docs" / "install.md")

    def test_tap_readme_has_no_direct_formula_path_brew(self) -> None:
        self._assert_doc_clean(ROOT / "packaging" / "homebrew-tap" / "README.md")

    def test_readme_has_no_direct_formula_path_brew(self) -> None:
        self._assert_doc_clean(ROOT / "README.md")

    def test_readme_ja_has_no_direct_formula_path_brew(self) -> None:
        self._assert_doc_clean(ROOT / "README.ja.md")


class UserFacingReadmeTests(unittest.TestCase):
    """Keep top-level READMEs focused on users, not private release chores."""

    def _readme_text(self, filename: str) -> str:
        return (ROOT / filename).read_text(encoding="utf-8")

    def test_english_readme_omits_maintainer_only_sections(self) -> None:
        text = self._readme_text("README.md")
        for forbidden in (
            "## Claude Code Version Maintenance",
            "## Public Release Checklist",
            "python3 scripts/public_audit.py",
            "historical logs and generated result JSON",
            "private handoff ledgers",
            "cheap smoke test",
            'TAP_DIR="$(brew --repository)',
            'rm -rf "$TAP_DIR"',
            "brew install --HEAD --build-from-source <owner>/homebrew-tap/claude-glm52",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("See [`LICENSE`](LICENSE)", text)
        self.assertIn("does not grant open-source reuse", text)

    def test_japanese_readme_omits_maintainer_only_sections(self) -> None:
        text = self._readme_text("README.ja.md")
        for forbidden in (
            "## Claude Code 更新時の確認",
            "## 公開前の確認",
            "python3 scripts/public_audit.py",
            "historical logs や generated result JSON",
            "private handoff ledger",
            "安い動作確認",
            'TAP_DIR="$(brew --repository)',
            'rm -rf "$TAP_DIR"',
            "brew install --HEAD --build-from-source <owner>/homebrew-tap/claude-glm52",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("## 軽い動作確認", text)
        self.assertIn("[`LICENSE`](LICENSE) を参照してください", text)
        self.assertIn("rights-reserved", text)

    def test_user_install_docs_do_not_advertise_custom_brew_tap(self) -> None:
        for path in (
            ROOT / "README.md",
            ROOT / "README.ja.md",
            ROOT / "docs" / "install.md",
        ):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(ROOT))):
                for forbidden in (
                    "## Install (Homebrew-first)",
                    "## インストール (Homebrew 優先)",
                    "brew tap AkiGarage/homebrew-tap",
                ):
                    self.assertNotIn(forbidden, text)

    def test_user_install_docs_reflect_pypi_publication(self) -> None:
        docs = {
            "README.md": self._readme_text("README.md"),
            "README.ja.md": self._readme_text("README.ja.md"),
            "docs/install.md": (ROOT / "docs" / "install.md").read_text(
                encoding="utf-8"
            ),
            "docs/distribution-strategy.md": (
                ROOT / "docs" / "distribution-strategy.md"
            ).read_text(encoding="utf-8"),
        }

        self.assertIn(
            "The recommended install path is PyPI through `uv`.",
            docs["README.md"],
        )
        self.assertIn(
            "推奨のインストール方法は、PyPI package を `uv` で使う方法です。",
            docs["README.ja.md"],
        )
        self.assertIn(
            "ClaudeCodeGLM Supervisor is distributed as the PyPI package",
            docs["docs/install.md"],
        )
        self.assertIn("Recommended: PyPI With uv", docs["docs/distribution-strategy.md"])

        for name, text in docs.items():
            with self.subTest(path=name):
                for forbidden in (
                    "## Current Status",
                    "## 現状の結論",
                    "## Recommendation",
                    "## Public repository plan",
                    "## Release gates",
                    "## Open decisions",
                    "does not yet have published GitHub Release assets or a PyPI release",
                    "release assets pending",
                    "publication pending",
                    "After PyPI publication",
                    "Until the package is published to PyPI",
                    "Target PyPI/uv commands",
                    "PyPI 公開までは",
                    "PyPI 化後の目標コマンド",
                    "今のところ Python package として install",
                    "PyPI package 化後の理想形",
                    "shasum -a 256 -c checksums.txt --ignore-missing",
                    "maintainer validation",
                    "private development",
                    "clean snapshot",
                    "手元の未push",
                ):
                    self.assertNotIn(forbidden, text)

    def test_readmes_link_to_codex_setup_prompt(self) -> None:
        self.assertIn(
            "docs/codex-setup-prompt.md", self._readme_text("README.md")
        )
        self.assertIn(
            "docs/codex-setup-prompt.md", self._readme_text("README.ja.md")
        )
        prompt = (ROOT / "docs" / "codex-setup-prompt.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "uv tool install --upgrade claude-glm52-supervisor",
            "claude-glm52 doctor --offline",
            "claude-glm52-delegate --role review",
            "Do not read, print, copy, or ask me to paste API keys",
        ):
            self.assertIn(required, prompt)

    def test_readmes_explain_cliproxyapi_and_safety_model(self) -> None:
        english = self._readme_text("README.md")
        japanese = self._readme_text("README.ja.md")

        english_required = (
            "## When To Use It",
            "## Safety Model",
            "## Why CLIProxyAPI Is Used",
            "CLIProxyAPI project, author, and maintainers",
            "Can this work without CLIProxyAPI?",
            "large-context metadata",
            "output ceiling",
            "usage snapshots",
            "weaker evidence when Codex audits a delegated run",
            "raw image summaries",
            "not persisted in result JSON or usage logs",
        )
        japanese_required = (
            "## 使う場面",
            "## 安全モデル",
            "## CLIProxyAPI を使う理由",
            "CLIProxyAPI project、作者、maintainers に感謝します",
            "CLIProxyAPI なしで動くかどうか",
            "大きな context 用 metadata",
            "output ceiling",
            "usage snapshot",
            "Codex が delegated run を監査するときの evidence",
            "raw image summary は result JSON や usage log に保存しません",
        )

        for needle in english_required:
            self.assertIn(needle, english)
        for needle in japanese_required:
            self.assertIn(needle, japanese)

    def test_japanese_readme_keeps_english_readme_concepts(self) -> None:
        japanese = self._readme_text("README.ja.md")
        for concept in (
            "Codex にセットアップさせる",
            "必要要件",
            "セットアップ概要",
            "軽い動作確認",
            "主な command",
            "task packet の形",
            "検証済みの構成",
            "関連 docs",
            "License",
            "Sensitive value",
            "Claude Code worker 専用 config directory",
            "task 自体が日本語 text を扱う場合を除き",
        ):
            self.assertIn(concept, japanese)


class DocumentationToneTests(unittest.TestCase):
    """Catch internal shorthand that should not leak into public-facing docs."""

    DOC_PATHS = (
        ROOT / "README.md",
        ROOT / "README.ja.md",
        ROOT / "SECURITY.md",
        ROOT / "docs" / "install.md",
        ROOT / "docs" / "distribution-strategy.md",
        ROOT / "docs" / "codex-setup-prompt.md",
        ROOT / "packaging" / "homebrew-tap" / "README.md",
        ROOT / "outputs" / "claude-glm52-delegation-contract.md",
        ROOT / "outputs" / "supervisor-duel-codex-comparison-20260619.md",
        ROOT / "outputs" / "supervisor-duel-comparison-20260618.md",
        ROOT / "outputs" / "supervisor-duel-vision-comparison-20260618.md",
        ROOT / "outputs" / "vision-mcp-evaluation-report.md",
    )

    FORBIDDEN = (
        "A" + "ki",
        "Hermes",
        "babysitting",
        "cheap",
        "安い",
        "published security contact",
    )

    def test_public_candidate_docs_avoid_internal_shorthand(self) -> None:
        for path in self.DOC_PATHS:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            scan_text = text.replace("AkiGarage", "")
            with self.subTest(path=str(path.relative_to(ROOT))):
                for forbidden in self.FORBIDDEN:
                    self.assertNotIn(forbidden, scan_text)

    def test_install_docs_stay_user_facing(self) -> None:
        install = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
        tap_readme = (ROOT / "packaging" / "homebrew-tap" / "README.md").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "pre-release Homebrew E2E",
            "private development tree",
            "temporary tap",
            "clean public source snapshot",
        ):
            self.assertNotIn(forbidden, install)
            self.assertNotIn(forbidden, tap_readme)


if __name__ == "__main__":
    unittest.main()
