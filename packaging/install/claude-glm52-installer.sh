#!/usr/bin/env bash
# Install ClaudeCodeGLM Supervisor from a GitHub Release tarball.
#
# The installer verifies checksums before extraction, installs into a user
# prefix, and writes only thin CLI wrappers. It does not read or mutate Claude
# Code, CLIProxyAPI, shell profile, or provider configuration.

set -euo pipefail

REPO="${CLAUDE_GLM52_INSTALL_REPO:-AkiGarage/ClaudeCodeGLM-supervisor}"
PREFIX="${HOME}/.local"
VERSION="latest"
DRY_RUN=0
UNINSTALL=0
FORCE=0

PACKAGE_BASENAME="claude-glm52-supervisor"
INSTALL_ROOT_NAME="claude-glm52"
CHECKSUMS_NAME="checksums.txt"

COMMANDS="
claude-glm52:claude_glm52_supervisor.cli
claude-glm52-delegate:claude_glm52_supervisor.delegate
claude-glm52-batch:claude_glm52_supervisor.batch
claude-glm52-subagent:claude_glm52_supervisor.subagent
claude-glm52-reviewer:claude_glm52_supervisor.reviewer
"

usage() {
  cat <<'USAGE'
Usage: claude-glm52-installer.sh [options]

Options:
  --prefix DIR       Install under DIR (default: $HOME/.local)
  --version TAG      Install a release tag such as v0.1.0 (default: latest)
  --repo OWNER/REPO  GitHub repository (default: AkiGarage/ClaudeCodeGLM-supervisor)
  --dry-run          Print the planned actions without network or file writes
  --force            Allow wrapper refresh when a different version is active
  --uninstall        Remove installed wrappers and package files under prefix
  -h, --help         Show this help
USAGE
}

say() {
  printf '%s\n' "$*"
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

version_without_v() {
  printf '%s' "${1#v}"
}

asset_name_for_version() {
  printf '%s-%s.tar.gz' "$PACKAGE_BASENAME" "$(version_without_v "$1")"
}

release_base_url() {
  if [ "$1" = "latest" ]; then
    printf 'https://github.com/%s/releases/latest/download' "$REPO"
  else
    printf 'https://github.com/%s/releases/download/%s' "$REPO" "$1"
  fi
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --prefix)
        [ "$#" -ge 2 ] || die "--prefix requires a value"
        PREFIX="$2"
        shift 2
        ;;
      --version)
        [ "$#" -ge 2 ] || die "--version requires a value"
        VERSION="$2"
        shift 2
        ;;
      --repo)
        [ "$#" -ge 2 ] || die "--repo requires a value"
        REPO="$2"
        shift 2
        ;;
      --dry-run)
        DRY_RUN=1
        shift
        ;;
      --force)
        FORCE=1
        shift
        ;;
      --uninstall)
        UNINSTALL=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "unknown option: $1"
        ;;
    esac
  done
}

resolve_latest_version() {
  need_command curl
  tag="$(
    curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" |
      sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' |
      head -n 1
  )"
  [ -n "$tag" ] || die "could not resolve latest release tag for ${REPO}"
  printf '%s' "$tag"
}

verify_checksum() {
  archive_path="$1"
  checksums_path="$2"
  archive_name="$(basename "$archive_path")"
  expected="$(
    awk -v file="$archive_name" '
      {
        name = $2
        sub(/^\*/, "", name)
        sub(/^\.\//, "", name)
        if (name == file) {
          print $1
          exit
        }
      }
    ' "$checksums_path"
  )"
  [ -n "$expected" ] || die "${checksums_path} does not contain ${archive_name}"
  actual="$(shasum -a 256 "$archive_path" | awk '{print $1}')"
  [ "$actual" = "$expected" ] || die "checksum mismatch for ${archive_name}"
}

write_wrapper() {
  name="$1"
  module="$2"
  install_dir="$3"
  bin_dir="$4"
  wrapper="${bin_dir}/${name}"
  cat >"$wrapper" <<EOF
#!/usr/bin/env sh
export PYTHONPATH="${install_dir}/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m ${module} "\$@"
EOF
  chmod 0755 "$wrapper"
}

install_wrappers() {
  install_dir="$1"
  bin_dir="${PREFIX}/bin"
  mkdir -p "$bin_dir"
  printf '%s\n' "$COMMANDS" |
    while IFS=: read -r name module; do
      [ -n "$name" ] || continue
      write_wrapper "$name" "$module" "$install_dir" "$bin_dir"
    done
}

safe_remove_tree() {
  path="$1"
  [ -n "$path" ] || die "refusing to remove an empty path"
  case "$path" in
    "$PREFIX"/share/"$INSTALL_ROOT_NAME"|"$PREFIX"/share/"$INSTALL_ROOT_NAME"/*)
      rm -R "$path"
      ;;
    *)
      die "refusing to remove unexpected path: $path"
      ;;
  esac
}

uninstall() {
  bin_dir="${PREFIX}/bin"
  install_root="${PREFIX}/share/${INSTALL_ROOT_NAME}"
  printf '%s\n' "$COMMANDS" |
    while IFS=: read -r name module; do
      [ -n "$name" ] || continue
      rm -f "${bin_dir}/${name}"
    done
  if [ -d "$install_root" ]; then
    safe_remove_tree "$install_root"
  fi
  say "Removed ClaudeCodeGLM Supervisor wrappers from ${bin_dir}"
}

install_release() {
  need_command curl
  need_command tar
  need_command shasum
  need_command python3

  resolved_version="$VERSION"
  if [ "$resolved_version" = "latest" ]; then
    resolved_version="$(resolve_latest_version)"
  fi

  base_url="$(release_base_url "$resolved_version")"
  archive_name="$(asset_name_for_version "$resolved_version")"
  install_root="${PREFIX}/share/${INSTALL_ROOT_NAME}"
  install_dir="${install_root}/${resolved_version}"
  current_file="${install_root}/current-version"

  if [ -f "$current_file" ]; then
    current_version="$(cat "$current_file")"
    if [ "$current_version" != "$resolved_version" ] && [ "$FORCE" -ne 1 ]; then
      say "Active version is ${current_version}; installing ${resolved_version}."
      say "Rerun with --force after confirming the version change."
      exit 2
    fi
  fi

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/claude-glm52-install.XXXXXX")"
  cleanup() {
    if [ -n "${tmpdir:-}" ] && [ -d "$tmpdir" ]; then
      rm -R "$tmpdir"
    fi
  }
  trap cleanup EXIT

  archive_path="${tmpdir}/${archive_name}"
  checksums_path="${tmpdir}/${CHECKSUMS_NAME}"
  curl -fsSL -o "$archive_path" "${base_url}/${archive_name}"
  curl -fsSL -o "$checksums_path" "${base_url}/${CHECKSUMS_NAME}"
  verify_checksum "$archive_path" "$checksums_path"

  mkdir -p "$install_root"
  if [ ! -d "$install_dir" ]; then
    extract_dir="${tmpdir}/extract"
    staging_dir="${install_root}/.install-${resolved_version}.$$"
    mkdir -p "$extract_dir" "$staging_dir"
    tar -xzf "$archive_path" -C "$extract_dir"
    source_dir="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    [ -n "$source_dir" ] && [ -d "${source_dir}/src/claude_glm52_supervisor" ] ||
      die "release archive does not contain src/claude_glm52_supervisor"
    cp -R "${source_dir}/." "$staging_dir/"
    mv "$staging_dir" "$install_dir"
  fi

  install_wrappers "$install_dir"
  printf '%s\n' "$resolved_version" >"$current_file"

  say "Installed ClaudeCodeGLM Supervisor ${resolved_version}"
  say "Binaries: ${PREFIX}/bin"
  if ! command -v claude-glm52 >/dev/null 2>&1; then
    say "Add ${PREFIX}/bin to PATH if claude-glm52 is not found."
  fi
  "${PREFIX}/bin/claude-glm52" doctor --offline
  say "Next: claude-glm52 setup --print"
}

dry_run() {
  version="$VERSION"
  base_url="$(release_base_url "$version")"
  archive_name="$([ "$version" = "latest" ] && printf '%s-latest.tar.gz' "$PACKAGE_BASENAME" || asset_name_for_version "$version")"
  say "Dry run:"
  say "  repo: ${REPO}"
  say "  version: ${VERSION}"
  say "  prefix: ${PREFIX}"
  say "  archive: ${base_url}/${archive_name}"
  say "  checksums: ${base_url}/${CHECKSUMS_NAME}"
  say "  install root: ${PREFIX}/share/${INSTALL_ROOT_NAME}"
  say "  wrappers: ${PREFIX}/bin/claude-glm52*"
}

parse_args "$@"

if [ "$DRY_RUN" -eq 1 ]; then
  dry_run
  exit 0
fi

if [ "$UNINSTALL" -eq 1 ]; then
  uninstall
else
  install_release
fi
