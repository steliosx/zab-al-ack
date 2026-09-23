"""Verify the exact pure-Python artifact used by the isolated service lab.

`check_installed` runs inside the interpreter that installed the wheel and
compares the whole installation with the wheel: every wheel member's bytes,
the package directory's inventory (nothing added, no compiled bytecode), the
dist-info inventory (only what pip itself adds), no other top-level entry
carrying the project's name, only the distributions `requirements.lock` pins
(plus pip and the project), every hashed RECORD entry of those distributions
equal to its file, no `sitecustomize`/`usercustomize` in any form, and every
`.pth` file owned by a verified RECORD entry. The runner probe calls it with
`python -I -S`, so no `.pth` file runs before the check.
"""
from __future__ import annotations

from email.parser import BytesParser
import base64
import csv
import hashlib
import importlib
import importlib.metadata
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tomllib
import zipfile

MAX_BYTES = 32 * 1024 * 1024
PIP_ADDED = ("INSTALLER", "REQUESTED", "RECORD", "direct_url.json")
SITE_CUSTOMISATION = ("sitecustomize", "usercustomize")
# The only startup hook the runner environment may carry, with its exact bytes: setuptools'
# distutils-precedence.pth from the setuptools 83.0.0 wheel that requirements.lock pins (PyPI
# wheel sha256 29b23c360f22f414dc7336bb39178cc7bcbf6021ed2733cde173f09dba19abb3). A dependency's
# RECORD is written by pip into the same writable site-packages, so it cannot authenticate a
# .pth it lists; this allow-list does not rely on it.
APPROVED_STARTUP_HOOKS = {"distutils-precedence.pth": "2638ce9e2500e572a5e0de7faed6661eb569d1b696fcba07b0dd223da5f5d224"}
BOOTSTRAP = {"pip"}  # the venv's own installer, not a requirements.lock pin


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def wheel_bytes(path, expected):
    require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), "invalid_wheel_digest")
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), "unsafe_wheel_path")
    require(path.stat().st_size <= MAX_BYTES, "wheel_too_large")
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected, "wheel_digest_mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        require(len(entries) <= 256 and len({e.filename for e in entries}) == len(entries), "wheel_member_count")
        require(sum(e.file_size for e in entries) <= MAX_BYTES, "wheel_contents_too_large")
        contents = {}
        for entry in entries:
            name = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            require(not name.is_absolute() and name.as_posix() == entry.filename and ".." not in name.parts
                    and "\\" not in entry.filename and not entry.is_dir()
                    and stat.S_IFMT(mode) in (0, stat.S_IFREG), "unsafe_wheel_member")
            contents[entry.filename] = archive.read(entry)
    metadata_names = [n for n in contents if n.endswith(".dist-info/METADATA")]
    require(len(metadata_names) == 1, "wheel_metadata_count")
    metadata = BytesParser().parsebytes(contents[metadata_names[0]])
    version = metadata.get("Version", "")
    require(metadata.get_all("Name") == ["zabalack"] and metadata.get_all("Version") == [version]
            and re.fullmatch(r"[0-9][A-Za-z0-9.!+_-]*", version), "wheel_project_identity")
    dist = "zabalack-" + version + ".dist-info"
    require(metadata_names[0] == dist + "/METADATA"
            and path.name == "zabalack-" + version + "-py3-none-any.whl", "wheel_filename_identity")
    require(all(n.startswith(("zabalack/", dist + "/")) for n in contents), "unexpected_wheel_payload")
    require("zabalack/__init__.py" in contents and dist + "/entry_points.txt" in contents, "wheel_package_missing")
    wheel = BytesParser().parsebytes(contents[dist + "/WHEEL"])
    require(wheel.get("Root-Is-Purelib") == "true" and wheel.get_all("Tag") == ["py3-none-any"], "wheel_platform_mismatch")
    return data, contents, {"filename": path.name, "sha256": expected, "version": version}


def package_files(directory, bytecode_allowed=True):
    require(directory.is_dir() and not directory.is_symlink(), "package_directory_missing")
    files = {}
    for path in directory.rglob("*"):
        require(not path.is_symlink(), "linked_package_member")
        if "__pycache__" in path.relative_to(directory).parts or path.suffix in (".pyc", ".pyo"):
            # Bytecode would run instead of the verified source; the runner
            # environment installs the wheel with --no-compile, so none may exist.
            require(bytecode_allowed or path.is_dir(), "installed_package_bytecode")
            continue
        if path.is_file():
            files["zabalack/" + path.relative_to(directory).as_posix()] = path.read_bytes()
    return files


def check_source(path, expected, root):
    data, contents, identity = wheel_bytes(path, expected)
    package = {n: value for n, value in contents.items() if n.startswith("zabalack/")}
    require(package_files(root / "src/zabalack") == package, "wheel_source_mismatch")
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    require(project["name"] == "zabalack" and project["version"] == identity["version"], "wheel_project_mismatch")
    return data, identity


def dist_info_files(directory):
    require(directory.is_dir() and not directory.is_symlink(), "installed_distribution_metadata_missing")
    files = set()
    for path in directory.rglob("*"):
        require(not path.is_symlink(), "linked_dist_info_member")
        if path.is_file():
            files.add(directory.name + "/" + path.relative_to(directory).as_posix())
    return files


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_distributions(checkout):
    """name -> version pinned by the checkout's requirements.lock (the runner environment installs exactly these)."""
    pins = {}
    for line in (checkout / "requirements.lock").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, sep, version = line.partition("==")
            require(sep and name and version, "requirements_lock_unreadable")
            pins[canonical(name)] = version.strip()
    return pins


def verified_records(location, except_dist, pins):
    """Every other distribution is pinned (or pip) and each hashed RECORD entry equals its file.

    Returns the site-packages-relative files those RECORDs own with a verified
    hash, and every top-level site-packages name any of those RECORDs lists.
    """
    owned, top_level = set(), set()
    for candidate in sorted(location.glob("*.dist-info")):
        if candidate.name == except_dist:
            continue
        require(candidate.is_dir() and not candidate.is_symlink(), "linked_dist_info")
        name, _, version = candidate.name[:-len(".dist-info")].rpartition("-")
        require(canonical(name) in BOOTSTRAP or pins.get(canonical(name)) == version, "unexpected_installed_distribution")
        record = candidate / "RECORD"
        require(record.is_file() and not record.is_symlink(), "installed_distribution_record_missing")
        for row in csv.reader(record.read_text(encoding="utf-8").splitlines()):
            if not row or not row[0]:
                continue
            relative = PurePosixPath(row[0])
            if relative.parts[0] == ".." or relative.is_absolute():
                continue  # launchers outside site-packages (bin/)
            top_level.add(relative.parts[0])
            hashspec = row[1] if len(row) > 1 else ""
            if not hashspec:
                continue  # RECORD itself and generated bytecode carry no hash
            algorithm, _, value = hashspec.partition("=")
            require(algorithm == "sha256", "installed_record_hash_algorithm")
            target = location / relative
            require(target.is_file() and not any(p.is_symlink() for p in (target, *target.parents)), "installed_distribution_file_missing")
            actual = base64.urlsafe_b64encode(hashlib.sha256(target.read_bytes()).digest()).rstrip(b"=").decode()
            require(actual == value, "installed_distribution_file_modified")
            owned.add(relative.as_posix())
    return owned, top_level


def site_customisation(location):
    """sitecustomize/usercustomize in any form (module, package, bytecode): Python imports them at start."""
    found = [path.name for path in location.iterdir() if path.name.split(".")[0] in SITE_CUSTOMISATION]
    cache = location / "__pycache__"
    if cache.is_dir():
        found += [path.name for path in cache.iterdir() if path.name.split(".")[0] in SITE_CUSTOMISATION]
    return sorted(found)


def startup_hooks(location):
    """`.pth` files directly in site-packages: the site module runs their import lines at start."""
    return sorted(path.name for path in location.iterdir() if path.suffix == ".pth")


def runner_site_paths():
    """The venv's site-packages for a probe run with -I -S (which skips the site module and its venv prefix)."""
    import sys
    import sysconfig
    env = Path(sys.executable).parent.parent
    require((env / "pyvenv.cfg").is_file(), "runner_not_a_venv")
    # The venv scheme explicitly: without the site module sys.prefix is the base
    # interpreter's, and Debian's patched default scheme would then name
    # <venv>/local/lib/pythonX.Y/dist-packages (observed on Ubuntu 24.04).
    paths = list(dict.fromkeys(sysconfig.get_path(key, scheme="venv", vars={"base": str(env), "platbase": str(env)})
                               for key in ("purelib", "platlib")))
    require(all(Path(path).is_dir() for path in paths), "runner_site_packages_missing")
    return paths


def check_installed(path, expected, checkout):
    _, contents, identity = wheel_bytes(path, expected)
    dist = importlib.metadata.distribution("zabalack")
    require(dist.version == identity["version"], "installed_version_mismatch")
    location = Path(dist.locate_file("")).resolve()
    require(not location.is_relative_to(checkout.resolve()), "installed_from_checkout")
    direct = dist.read_text("direct_url.json")
    require(not direct or not json.loads(direct).get("dir_info", {}).get("editable"), "editable_install")
    dist_dir = "zabalack-" + identity["version"] + ".dist-info"
    for name, value in contents.items():
        if name.endswith(".dist-info/RECORD"):
            continue  # pip rewrites RECORD for generated launchers and metadata.
        target = location / name
        require(not any(p.is_symlink() for p in (target, *target.parents))
                and target.read_bytes() == value, "installed_wheel_bytes_mismatch")
    require(package_files(location / "zabalack", bytecode_allowed=False)
            == {n: v for n, v in contents.items() if n.startswith("zabalack/")}, "installed_package_inventory_mismatch")
    # The dist-info holds the wheel's metadata members and what pip adds on install, nothing else.
    allowed = {n for n in contents if n.startswith(dist_dir + "/")} | {dist_dir + "/" + n for n in PIP_ADDED}
    require(dist_info_files(location / dist_dir) <= allowed, "installed_dist_info_inventory_mismatch")
    # No other top-level entry carries the project's name (a second package, a .pth, a stray dist-info).
    require({p.name for p in location.iterdir() if p.name.lower().startswith("zabalack")} == {"zabalack", dist_dir},
            "unexpected_installed_entries")
    # Only the pinned distributions, each file equal to its RECORD hash; no site
    # customisation at all; a .pth only when a verified RECORD entry owns it
    # (for example setuptools' distutils-precedence.pth).
    owned, top_level = verified_records(location, dist_dir, locked_distributions(checkout))
    require(not site_customisation(location), "site_customisation_present")
    unowned = [name for name in startup_hooks(location) if name not in owned]
    require(not unowned, "unowned_startup_hook")
    unapproved = [name for name in startup_hooks(location)
                  if APPROVED_STARTUP_HOOKS.get(name) != hashlib.sha256((location / name).read_bytes()).hexdigest()]
    require(not unapproved, "unapproved_startup_hook")
    # Nothing at the top of site-packages that no installed distribution owns.
    unowned_entries = {path.name for path in location.iterdir()} - top_level - {"zabalack", dist_dir, "__pycache__"}
    require(not unowned_entries, "unowned_installed_entry")
    module = importlib.import_module("zabalack")
    require(Path(module.__file__).resolve() == location / "zabalack/__init__.py", "wrong_import_origin")
    # installed_bytes_verified covers the supplied wheel's own files, anchored by its digest.
    # Dependency files are only checked against their installed RECORD, which is not anchored.
    return {**identity, "installed_bytes_verified": True, "startup_hooks": startup_hooks(location),
            "dependency_verification": "record_consistency_only"}
