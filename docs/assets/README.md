# Zabalack illustrations

These illustrations accompany the **Ack Relay** identity in
[the brand guide](../brand/BRAND.md).

| File | Purpose |
| --- | --- |
| `readme-hero.png`, `readme-hero-dark.png` | Light and dark editorial artwork: one amber route and the line “One action. Carried through.” |
| `badge-alpha.svg`, `badge-python.svg`, `badge-postgresql.svg`, `badge-license.svg` | Static project information for the README navigation; no live CI or release result is implied |
| `acknowledgement-journey.svg` | A high-level view of human action, durable capture, verification and bounded delivery |
| `delivery-outcomes.svg` | Three observed situations: already acknowledged, eligible delivery and an unknown outcome |

The heroes are generated brand artwork derived from the original Alrust style
reference and the canonical Zabalack vector identity. They are not interfaces,
architecture diagrams or records of a live delivery. Use the actual SVG logo files
for standalone logo placement; the heroes are illustrations. The README uses
picture sources to select the light or dark hero.

The two diagrams use exact vector labels and the canonical mark. They describe
the alpha's single-alert acknowledgement design. They add no new implementation
states or compatibility claims, and do not establish a release or a passed gate.
The repository README and design documentation retain the detailed scope and
evaluation evidence.

## Editing and reproduction

From the repository root:

```bash
python3 docs/brand/generate_brand.py
python3 docs/assets/generate_readme_diagrams.py
```

Core SVG generation uses the Python standard library. Add `--png` to the diagram
command to produce PNG previews with Inkscape. The logo generator's optional
`--render` previews require Inkscape and Pillow. These checks concern the artwork,
not the application's runtime or conformance tests.

If the canonical mark changes, update the diagram generator's embedded mark
from `../brand/zabalack-mark.svg` and check the resulting illustrations together.
Each exported SVG is standalone, with no scripts or external assets.

Project-owned branding and illustrations are supplied under the repository's
[Apache-2.0 license](../../LICENSE). Alerta and Zabbix are named to identify the
integration; the artwork does not assert their endorsement.
