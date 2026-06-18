#!/usr/bin/env bash
# Build GitHub Release assets from a clean repository snapshot.

set -euo pipefail

VERSION=""
OUT_DIR="dist/release"
REF="HEAD"
SIGN="none"
ALLOW_DIRTY=0
DRY_RUN=0

PACKAGE_BASENAME="claude-glm52-supervisor"
INSTALLER="claude-glm52-installer.sh"

usage() {
  cat <<'USAGE'
Usage: build-release-assets.sh --version vX.Y.Z [options]

Options:
  --version TAG       Release tag, for example v0.1.0
  --out-dir DIR       Output directory (default: dist/release)
  --ref REF           Git ref to archive (default: HEAD)
  --sign MODE         none, minisign, or gpg (default: none)
  --allow-dirty       Allow building from a dirty worktree
  --dry-run           Print planned actions only
  -h, --help          Show this help
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

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --version)
        [ "$#" -ge 2 ] || die "--version requires a value"
        VERSION="$2"
        shift 2
        ;;
      --out-dir)
        [ "$#" -ge 2 ] || die "--out-dir requires a value"
        OUT_DIR="$2"
        shift 2
        ;;
      --ref)
        [ "$#" -ge 2 ] || die "--ref requires a value"
        REF="$2"
        shift 2
        ;;
      --sign)
        [ "$#" -ge 2 ] || die "--sign requires a value"
        SIGN="$2"
        shift 2
        ;;
      --allow-dirty)
        ALLOW_DIRTY=1
        shift
        ;;
      --dry-run)
        DRY_RUN=1
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

check_inputs() {
  [ -n "$VERSION" ] || die "--version is required"
  case "$VERSION" in
    v[0-9]*.[0-9]*.[0-9]*) ;;
    *) die "--version must look like vX.Y.Z" ;;
  esac
  case "$SIGN" in
    none|minisign|gpg) ;;
    *) die "--sign must be none, minisign, or gpg" ;;
  esac
}

check_clean_tree() {
  [ "$ALLOW_DIRTY" -eq 1 ] && return 0
  if [ -n "$(git status --porcelain)" ]; then
    die "worktree is dirty; commit the clean public snapshot or pass --allow-dirty"
  fi
}

sign_asset() {
  path="$1"
  case "$SIGN" in
    none)
      return 0
      ;;
    minisign)
      need_command minisign
      minisign -Sm "$path"
      ;;
    gpg)
      need_command gpg
      gpg --detach-sign --armor "$path"
      ;;
  esac
}

build_assets() {
  need_command git
  need_command gzip
  need_command shasum
  check_clean_tree

  version_no_v="${VERSION#v}"
  archive="${PACKAGE_BASENAME}-${version_no_v}.tar.gz"
  prefix="ClaudeCodeGLM-supervisor-${version_no_v}/"

  mkdir -p "$OUT_DIR"
  git archive --format=tar --prefix="$prefix" "$REF" | gzip -n >"${OUT_DIR}/${archive}"
  cp "packaging/install/${INSTALLER}" "${OUT_DIR}/${INSTALLER}"
  chmod 0755 "${OUT_DIR}/${INSTALLER}"

  (
    cd "$OUT_DIR"
    shasum -a 256 "$archive" "$INSTALLER" >checksums.txt
  )

  sign_asset "${OUT_DIR}/${archive}"
  sign_asset "${OUT_DIR}/${INSTALLER}"
  say "Wrote release assets to ${OUT_DIR}"
}

dry_run() {
  version_no_v="${VERSION#v}"
  say "Dry run:"
  say "  version: ${VERSION}"
  say "  ref: ${REF}"
  say "  out dir: ${OUT_DIR}"
  say "  archive: ${PACKAGE_BASENAME}-${version_no_v}.tar.gz"
  say "  installer: ${INSTALLER}"
  say "  checksums: checksums.txt"
  say "  signing: ${SIGN}"
}

parse_args "$@"
check_inputs

if [ "$DRY_RUN" -eq 1 ]; then
  dry_run
else
  build_assets
fi
