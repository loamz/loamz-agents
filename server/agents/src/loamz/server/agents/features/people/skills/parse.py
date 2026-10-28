"""Reading a SKILL.md and scanning an upload. Pure functions: no storage, no network."""

from __future__ import annotations

import io
import re
import zipfile

import yaml

from loamz.server.agents.features.people.skills.model import (
    MAX_DESCRIPTION,
    MAX_INSTRUCTIONS,
    MAX_NAME,
    ScanFinding,
)

MAX_UPLOAD_BYTES = 1_000_000
MAX_FILES = 40
TEXT_SUFFIXES = (".md", ".txt", ".csv", ".json", ".yaml", ".yml")
"""Reference files a skill may carry. Scripts and binaries are refused: a skill is instructions, never code that runs."""

OVERRIDES = [
    re.compile(p, re.I)
    for p in (
        r"ignore (all |any |your |the )?(previous|prior|above|earlier) (instructions|rules)",
        r"disregard (all |any |your |the )?(previous|prior|above|earlier)",
        r"you are (now )?(dan|jailbroken|unrestricted|uncensored)",
        r"reveal (your|the) (system )?prompt",
        r"(exfiltrate|send) (the )?(user'?s? )?(data|memories|files|tokens?) to",
    )
]


class SkillRefused(ValueError):
    """The upload was refused; `findings` says why, file by file."""

    def __init__(self, findings: list[ScanFinding]) -> None:
        super().__init__("; ".join(f"{f.file}: {f.problem}" for f in findings))
        self.findings = findings


def parse_skill_md(text: str, fallback_name: str = "") -> tuple[str, str, str]:
    """`(name, description, instructions)` from a SKILL.md: YAML front matter with a name and a description, then the body."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text.lstrip("﻿"), re.S)
    if not match:
        raise ValueError(
            "The file must start with a header that has the skill's name and description."
        )
    try:
        head = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError("The header at the top of the file is not valid YAML.") from exc
    if not isinstance(head, dict):
        raise ValueError("The header at the top of the file is not valid YAML.")
    name = str(head.get("name") or fallback_name).strip()
    description = str(head.get("description") or "").strip()
    body = match.group(2).strip()
    if not name:
        raise ValueError("The header needs a name.")
    if not description:
        raise ValueError("The header needs a description.")
    if len(name) > MAX_NAME:
        raise ValueError(f"The name is longer than {MAX_NAME} characters.")
    if len(description) > MAX_DESCRIPTION:
        raise ValueError(f"The description is longer than {MAX_DESCRIPTION} characters.")
    if not body:
        raise ValueError("The skill has no instructions below the header.")
    return name, description, body


def scan_text(file: str, text: str) -> list[ScanFinding]:
    return [
        ScanFinding(file=file, problem="tries to override the assistant's rules")
        for pattern in OVERRIDES
        if pattern.search(text)
    ]


def _safe_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return not path.startswith(("/", "\\")) and ".." not in parts and ":" not in parts[0]


def read_upload(filename: str, data: bytes) -> list[tuple[str, str, str]]:
    """The skills in an uploaded `.md`, `.zip` or `.skill` file, after scanning. Raises `SkillRefused` with every problem found."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise SkillRefused([ScanFinding(file=filename, problem="is larger than 1 MB")])
    lower = filename.lower()
    if lower.endswith(".md"):
        return [_one(filename, data.decode("utf-8", errors="replace"), {})]
    if not lower.endswith((".zip", ".skill")):
        raise SkillRefused(
            [ScanFinding(file=filename, problem="is not a .md, .zip or .skill file")]
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillRefused([ScanFinding(file=filename, problem="is not a valid zip file")]) from exc
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        findings: list[ScanFinding] = []
        if len(infos) > MAX_FILES:
            raise SkillRefused(
                [ScanFinding(file=filename, problem=f"holds more than {MAX_FILES} files")]
            )
        if sum(i.file_size for i in infos) > MAX_UPLOAD_BYTES * 3:
            raise SkillRefused([ScanFinding(file=filename, problem="unpacks to too much data")])
        texts: dict[str, str] = {}
        for info in infos:
            if not _safe_path(info.filename):
                findings.append(ScanFinding(file=info.filename, problem="has an unsafe path"))
            elif info.filename.startswith("__MACOSX/") or info.filename.endswith(".DS_Store"):
                continue
            elif not info.filename.lower().endswith(TEXT_SUFFIXES):
                findings.append(
                    ScanFinding(
                        file=info.filename,
                        problem="is not a text file (scripts and programs are not allowed)",
                    )
                )
            else:
                texts[info.filename] = archive.read(info).decode("utf-8", errors="replace")
        roots = [p for p in texts if p.rsplit("/", 1)[-1].upper() == "SKILL.MD"]
        if not roots:
            findings.append(ScanFinding(file=filename, problem="has no SKILL.md"))
        if findings:
            raise SkillRefused(findings)
        folders = {r: (r.rsplit("/", 1)[0] + "/" if "/" in r else "") for r in roots}
        out = []
        for root in roots:
            folder = folders[root]
            # Files in this skill's folder, leaving out any other skill folder nested inside it.
            deeper = [
                f for r, f in folders.items() if r != root and f.startswith(folder) and f != folder
            ]
            extra = {
                path[len(folder) :]: text
                for path, text in texts.items()
                if path.startswith(folder)
                and path != root
                and not any(path.startswith(d) for d in deeper)
            }
            out.append(
                _one(root, texts[root], extra, fallback=folder.strip("/").rsplit("/", 1)[-1])
            )
        return out


def _one(file: str, text: str, extra: dict[str, str], fallback: str = "") -> tuple[str, str, str]:
    try:
        name, description, body = parse_skill_md(text, fallback)
    except ValueError as exc:
        raise SkillRefused([ScanFinding(file=file, problem=str(exc))]) from exc
    for path, more in sorted(extra.items()):
        body += f"\n\n## Reference: {path}\n\n{more.strip()}"
    findings = scan_text(file, text) + [f for p, t in extra.items() for f in scan_text(p, t)]
    if len(body) > MAX_INSTRUCTIONS:
        findings.append(
            ScanFinding(
                file=file, problem=f"has more than {MAX_INSTRUCTIONS} characters of instructions"
            )
        )
    if findings:
        raise SkillRefused(findings)
    return name, description, body
