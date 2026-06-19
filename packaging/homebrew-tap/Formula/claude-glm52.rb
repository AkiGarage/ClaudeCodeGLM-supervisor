# ClaudeCodeGLM Supervisor Homebrew formula.
#
# RELEASE NOTE:
# This advanced formula fetches from a public release archive and installs thin
# wrappers around the supervisor's Python and shell entry points.
#
# This formula only installs repo files and thin bin/ wrappers. It never
# writes secrets, never edits Claude Code global config, and never starts
# CLIProxyAPI. `brew test` uses only offline help/version commands.

class ClaudeGlm52 < Formula
  desc "Codex-safe Claude Code GLM-5.2 delegation umbrella"
  homepage "https://github.com/AkiGarage/claude-glm52"
  url "https://github.com/AkiGarage/claude-glm52/releases/download/v0.0.2/claude-glm52-0.0.2.tar.gz"
  sha256 "e320c4e95561884a6f2ba8466ab1cfac91a2485416ee55c861b5ce98dbfe160c"
  # The current LICENSE is a conservative rights-reserved notice, not a
  # standard SPDX expression. `:cannot_represent` is Homebrew's safe
  # "no SPDX claim" marker. Replace with a real SPDX expression only if a
  # future license grants matching reuse/redistribution rights.
  license :cannot_represent
  # `--HEAD` builds from the remote `main` head, not the local working tree.
  head "https://github.com/AkiGarage/claude-glm52.git", branch: "main"

  # The umbrella CLI and wrappers are Python 3 stdlib-only. We do not require
  # npm, pip, pnpm, or uvx at install time.
  depends_on "python@3.11"

  # No build steps are needed; we copy repo files into libexec.
  def install
    libexec.install Dir["*"]

    python_bin = Formula["python@3.11"].opt_bin/"python3.11"

    # Ensure bin/ exists before we write wrappers. On a fresh Cellar prefix
    # (e.g. first install of this formula) `bin/name`'s parent directory is
    # not created by libexec.install, and `atomic_write` raises
    # Errno::ENOENT on the missing `bin`.
    bin.mkpath

    # Thin bin/ wrappers. We avoid `write_env_script(target, {})` because the
    # source tarball may not preserve the executable bit on .py / .sh files.
    # Each wrapper execs the language runtime explicitly; the wrapper itself
    # is chmod'd to 0555 so it is directly runnable on PATH.
    {
      "claude-glm52"          => ["python", "outputs/claude-glm52.py"],
      "claude-glm52-delegate" => ["python", "outputs/claude-glm52-delegate.py"],
      "claude-glm52-batch"    => ["python", "outputs/claude-glm52-batch.py"],
      "claude-glm52-subagent" => ["bash",   "outputs/claude-glm52-subagent.sh"],
      "claude-glm52-reviewer" => ["bash",   "outputs/claude-glm52-reviewer.sh"],
    }.each do |name, (kind, rel)|
      target = libexec/rel
      body =
        case kind
        when "python"
          <<~SH
            #!/bin/bash
            exec "#{python_bin}" "#{target}" "$@"
          SH
        when "bash"
          <<~SH
            #!/bin/bash
            exec /bin/bash "#{target}" "$@"
          SH
        else
          odie "unknown wrapper kind: #{kind}"
        end
      wrapper = bin/name
      wrapper.atomic_write(body)
      wrapper.chmod(0555)
    end
  end

  def caveats
    <<~EOS
      ClaudeCodeGLM Supervisor installed.

      This formula only placed repo files and bin wrappers. It did NOT:
        - install or start Claude Code
        - install or start CLIProxyAPI
        - write any Z.AI API key or provider config
        - mutate ~/.claude or ~/.claude-glm52-worker

      Next steps (no secrets are written by these commands):
        claude-glm52 --version
        claude-glm52 doctor --offline   # safe checks, no network/secrets
        claude-glm52 setup --print      # manual guide, no mutation

      When you are ready to delegate real work:
        brew install --cask claude-code
        install CLIProxyAPI from its official release
        export CLAUDE_GLM52_WORKER_CONFIG_DIR="$HOME/.claude-glm52-worker"
        run: claude-glm52 doctor
    EOS
  end

  test do
    # Offline-only, no network, no Claude Code, no secrets.
    assert_match "claude-glm52 #{version}", shell_output("#{bin}/claude-glm52 --version")
    assert_match "repo_root", shell_output("#{bin}/claude-glm52 paths")
    assert_match "offline doctor", shell_output("#{bin}/claude-glm52 doctor --offline")
    assert_match "Manual setup guide", shell_output("#{bin}/claude-glm52 setup --print")
  end
end
