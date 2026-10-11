#!/usr/bin/env bash
# Build pinned PostgreSQL into a development prefix; no system installation.
set -euo pipefail
if [[ "${1:-}" != "--isolated-lab" ]]; then
  echo "Usage: lab/build_postgres.sh --isolated-lab [existing-postgresql-18.x.tar.bz2]" >&2
  exit 2
fi
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="${repo_dir}/../runtime"
source_archive="${2:-${repo_dir}/../upstream/postgresql-18.6.tar.bz2}"
# Accepted upstream release archives and their published SHA-256 values
# (https://ftp.postgresql.org/pub/source/v<version>/postgresql-<version>.tar.bz2.sha256).
# 18.6 is the reference pin (owner decision, 11 October 2026: latest 18.x minor).
# 18.0 and 18.3 remain accepted so historical evidence can be reproduced; 18.3 is
# also the engine version inside the pinned PGlite.
declare -A known_sha=(
  [18.0]=0d5b903b1e5fe361bca7aa9507519933773eb34266b1357c4e7780fdee6d6078
  [18.3]=d95663fbbf3a80f81a9d98d895266bdcb74ba274bcc04ef6d76630a72dee016f
  [18.6]=555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f
)
for needed_command in gcc make bison flex perl; do
  command -v "$needed_command" >/dev/null || { echo "Development dependency required: $needed_command" >&2; exit 3; }
done
mkdir -p "$runtime_dir/build"
if [[ ! -f "$source_archive" ]]; then
  echo "Download the public release archive explicitly, then rerun:" >&2
  echo "https://ftp.postgresql.org/pub/source/v18.6/postgresql-18.6.tar.bz2" >&2
  exit 3
fi
archive_name="$(basename -- "$source_archive")"
if [[ "$archive_name" =~ ^postgresql-(18\.[0-9]+)\.tar\.bz2$ ]]; then
  pg_version="${BASH_REMATCH[1]}"
else
  echo "Archive must be named postgresql-18.<patch>.tar.bz2" >&2
  exit 4
fi
source_sha="${known_sha[$pg_version]:-}"
[[ -n "$source_sha" ]] || { echo "PostgreSQL $pg_version is not in the accepted release list" >&2; exit 4; }
actual_sha="$(sha256sum "$source_archive" | cut -d ' ' -f 1)"
[[ "$actual_sha" == "$source_sha" ]] || { echo "Source checksum mismatch for $archive_name" >&2; exit 4; }
if [[ ! -d "$runtime_dir/build/postgresql-$pg_version" ]]; then
  tar --extract --bzip2 --no-same-owner --file "$source_archive" --directory "$runtime_dir/build"
fi
cd "$runtime_dir/build/postgresql-$pg_version"
./configure --prefix="$runtime_dir/pg18" --without-icu --without-readline --with-ssl=openssl
make -j "${ACK_LAB_BUILD_JOBS:-4}"
make install
"$runtime_dir/pg18/bin/postgres" --version
