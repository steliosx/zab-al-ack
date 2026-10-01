# Zab-al-ack — Ack Relay

This refined identity uses a single solid **Z** whose lower terminal rises into an acknowledgement cue. The open inner pocket and gently softened corners keep the silhouette clear at small sizes. Its shared typographic weight relates it to Alrust without borrowing Alrust's converging routes.

The symbol reflects one scoped handoff: durable acknowledgement delivery from one human action in Alerta to its original Zabbix problem. It does not represent many-alert aggregation, full synchronization, automatic re-acknowledgement or bidirectional writes.

## Names

Use **Zab-al-ack** as the official project name. The supplied visual wordmark reads **zabalack.**, with an amber period. This branding does not rename any package, repository, API or command.

## Palette

| Color | Hex | Role |
| --- | --- | --- |
| Amber | `#E59732` | Primary mark, punctuation and icon field |
| Navy | `#1C2936` | Primary lettering, dark surfaces and icon mark |
| Ivory | `#FAF9F6` | Warm light surfaces and reversed lettering |
| White | `#FFFFFF` | Neutral light surface |

## Assets

| File | Use |
| --- | --- |
| `zabalack-logo-light.svg` | Ivory or white backgrounds |
| `zabalack-logo-dark.svg` | Navy backgrounds |
| `zabalack-logo-mono.svg` | One-color navy output |
| `zabalack-mark.svg` | Standalone amber symbol |
| `zabalack-mark-dark.svg` | Ivory symbol on dark backgrounds |
| `zabalack-mark-mono.svg` | Standalone navy symbol |
| `favicon.svg` | 16–32 px browser and compact interface icon |
| `zabalack-app-icon.svg` | 512 px application or repository avatar master |

All logo and mark backgrounds are transparent. The favicon and app icon use an amber square with optical padding and a navy mark. All lettering is supplied as paths; no font installation is needed.

## Space and size

Keep at least **64 canonical mark units**, equal to the top bar's thickness, clear around the complete mark or lockup. The canonical mark is 372 × 320 units. At a 32 px mark width, this is about 5.5 px of clear space.

Use the standalone mark at **24 px wide or more**. At 16–23 px, use the supplied favicon composition. Use the full wordmark at **176 px wide or more**; recommended header widths are **208 px on desktop** and **180 px on mobile**. The supplied icon compositions already include their own internal padding.

Preserve the mark's proportions and open pocket. Keep the period. Do not stretch, rotate, outline, add gradients or shadows, fill the negative space, or attach a separate check badge. Use the appropriate contrast variant for the surrounding surface.

## Reproduction

Run `python generate_brand.py` to regenerate the eight SVGs, specification and this guide. Core generation needs only Python 3. Use `python generate_brand.py --render` to also generate the reference, contact sheet, and tiny-size proofs; that optional step requires Inkscape and Pillow.

The SVGs contain no raster imagery, scripts, fonts, filters or external resources. The generator writes UTF-8 explicitly and keeps the canonical silhouette and letter paths in one place.

## License

These original branding assets are project-owned and supplied under the project's **Apache-2.0** license. No trademark registration, certification or third-party affiliation is asserted. Alerta and Zabbix names identify the integrations described by the project.
