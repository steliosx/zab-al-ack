"""Verify the exact pure-Python artifact used by the isolated service lab.

`check_installed` runs inside the interpreter that installed the wheel and
compares the whole installation with the wheel: every wheel member's bytes,
the package directory's inventory (nothing added), the dist-info inventory
(only what pip itself adds), no other top-level entry carrying the project's
name, and every startup hook in site-packages (`.pth`, `sitecustomize`,
`usercustomize`) owned by another installed distribution's RECORD.
"""
from __future__ import annotations

from email.parser import BytesParser
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
STARTUP_HOOKS = ("sitecustomize.py", "usercustomize.py")


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


def package_files(directory):
    require(directory.is_dir() and not directory.is_symlink(), "package_directory_missing")
    files = {}
    for path in directory.rglob("*"):
        require(not path.is_symlink(), "linked_package_member")
        if "__pycache__" in path.relative_to(directory).parts:
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


def owned_files(location, except_dist):
    """Every file the other installed distributions' RECORDs claim, relative to site-packages."""
    owned = set()
    for candidate in location.glob("*.dist-info"):
        if candidate.name == except_dist or candidate.is_symlink():
            continue
        record = candidate / "RECORD"
        if not record.is_file():
            continue
        for line in record.read_text(encoding="utf-8").splitlines():
            name = line.split(",")[0]
            if name:
                owned.add(PurePosixPath(name).as_posix())
    return owned


def startup_hooks(location):
    """`.pth` files and site customisation modules directly in site-packages: the interpreter runs them at start."""
    return sorted(path.name for path in location.iterdir()
                  if path.is_file() and (path.suffix == ".pth" or path.name in STARTUP_HOOKS))


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
    require(package_files(location / "zabalack") == {n: v for n, v in contents.items() if n.startswith("zabalack/")},
            "installed_package_inventory_mismatch")
    # The dist-info holds the wheel's metadata members and what pip adds on install, nothing else.
    allowed = {n for n in contents if n.startswith(dist_dir + "/")} | {dist_dir + "/" + n for n in PIP_ADDED}
    require(dist_info_files(location / dist_dir) <= allowed, "installed_dist_info_inventory_mismatch")
    # No other top-level entry carries the project's name (a second package, a .pth, a stray dist-info).
    require({p.name for p in location.iterdir() if p.name.lower().startswith("zabalack")} == {"zabalack", dist_dir},
            "unexpected_installed_entries")
    # Every startup hook belongs to another installed distribution (for example setuptools' distutils-precedence.pth).
    unowned = [name for name in startup_hooks(location) if name not in owned_files(location, dist_dir)]
    require(not unowned, "unowned_startup_hook")
    module = importlib.import_module("zabalack")
    require(Path(module.__file__).resolve() == location / "zabalack/__init__.py", "wrong_import_origin")
    return {**identity, "installed_bytes_verified": True, "startup_hooks": startup_hooks(location)}
