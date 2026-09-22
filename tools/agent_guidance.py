"""Validate and fingerprint repository agent guidance separately from runtime code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

SKILLS = ('zabalack-release-review', 'zabalack-isolated-lab', 'zabalack-migration-review')
CONTEXT = ('AGENTS.md', 'CLAUDE.md', 'CONTRIBUTING.md', 'docs/agent-workflows.md',
           'docs/agents/astra.md', 'docs/agents/claude-code.md',
           'docs/RELEASING.md', 'docs/local-services.md', 'lab/real_zabbix.md',
           'docs/DESIGN.md', 'docs/final_review_notes.md', 'docs/SPEC_AMENDMENTS.md',
           'docs/deployment.md', 'docs/rollback.md', 'docs/INPUTS_REQUIRED.md',
           'evals/agent-workflows/cases.json')


def inspect(root: Path) -> dict:
    # CI preparation and secret scanning import the public-file helpers before
    # project dependencies are installed. Only guidance validation needs YAML.
    import yaml

    root = root.absolute()
    paths = [*CONTEXT, *(f'.agents/skills/{name}/SKILL.md' for name in SKILLS)]
    selected = set(paths)
    rows = []
    for name in paths:
        path = root / name
        if (not path.is_file() or any(p.is_symlink() for p in (path, *path.parents))
                or path.stat().st_size > 256 * 1024):
            raise ValueError('agent_guidance_file_invalid')
        data = path.read_bytes()
        text = data.decode('utf-8')
        if name.endswith('/SKILL.md'):
            if not text.startswith('---\n') or '\n---\n' not in text[4:]:
                raise ValueError('agent_guidance_frontmatter_invalid')
            header, body = text[4:].split('\n---\n', 1)
            try:
                metadata = yaml.safe_load(header)
            except yaml.YAMLError as error:
                raise ValueError('agent_guidance_frontmatter_invalid') from error
            if (not isinstance(metadata, dict) or set(metadata) != {'name', 'description'}
                    or metadata['name'] != path.parent.name
                    or not isinstance(metadata['description'], str)
                    or not 10 <= len(metadata['description']) <= 240 or not body.strip()):
                raise ValueError('agent_guidance_metadata_invalid')
            for link in re.findall(r'\]\(([^)]+)\)', body):
                target = (path.parent / link.split('#')[0]).resolve()
                if not target.is_relative_to(root) or target.relative_to(root).as_posix() not in selected:
                    raise ValueError('agent_guidance_reference_invalid')
        rows.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest()})
    rows.sort(key=lambda row: row['path'])
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'schema_version': 1, 'skills': list(SKILLS), 'files': rows, 'sha256': digest}


if __name__ == '__main__':
    print(json.dumps(inspect(Path(__file__).resolve().parents[1]), indent=2))
