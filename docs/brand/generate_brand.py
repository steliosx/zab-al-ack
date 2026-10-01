#!/usr/bin/env python3
"""Reproduce the Zabalack / Ack Relay original vector brand system.

Core generation uses Python's standard library only. All lettering is supplied
as purpose-drawn paths. Nothing is fetched, traced or loaded from a font.
Use --render for local Inkscape + Pillow contact sheets and small-size proofs.
"""
from __future__ import annotations
import argparse
import json
from math import hypot
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
AMBER = '#E59732'
NAVY = '#1C2936'
IVORY = '#FAF9F6'
WHITE = '#FFFFFF'
MARK_WIDTH = 372
MARK_HEIGHT = 320
LOGO_WIDTH = 1760
LOGO_HEIGHT = 320

# One continuous Z route. Its lower bar lifts into an acknowledgement terminal.
# Corners are quadratic fillets; the aperture stays deliberately open at 16 px.
MARK_VERTICES = [
    (0, 0), (304, 0), (304, 64), (128, 240), (206, 240),
    (312, 126), (372, 180), (242, 320), (0, 320),
    (0, 256), (192, 64), (0, 64),
]

def rounded_polygon(vertices, radius=8):
    out = []
    for i, p in enumerate(vertices):
        prev, nxt = vertices[i-1], vertices[(i+1) % len(vertices)]
        d1 = hypot(prev[0]-p[0], prev[1]-p[1])
        d2 = hypot(nxt[0]-p[0], nxt[1]-p[1])
        r = min(radius, d1/3, d2/3)
        a = (p[0]+(prev[0]-p[0])*r/d1, p[1]+(prev[1]-p[1])*r/d1)
        b = (p[0]+(nxt[0]-p[0])*r/d2, p[1]+(nxt[1]-p[1])*r/d2)
        out.append(f'{"M" if i == 0 else "L"}{a[0]:.3f} {a[1]:.3f}Q{p[0]:.3f} {p[1]:.3f} {b[0]:.3f} {b[1]:.3f}')
    return ''.join(out) + 'Z'

MARK_PATH = rounded_polygon(MARK_VERTICES)

# Purpose-drawn lettering, with Alrust's single-storey a and substantial stroke
# proportions carried across the family. Canonical letterforms are real paths.
GLYPHS = {
 'z': 'M0 73H145V109L61 184H145V221H0V185L84 110H0Z',
 'a': ('M71 70C90 70 104 77 116 89V73H158V221H116V207'
       'C104 219 89 224 70 224C29 224 0 193 0 148'
       'C0 104 29 70 71 70Z'
       'M80 109C59 109 43 125 43 148C43 171 59 185 80 185'
       'C102 185 117 170 117 148C117 125 102 109 80 109Z'),
 'b': ('M0 0H43V87C55 76 70 70 87 70C129 70 157 102 157 147'
       'C157 192 127 224 86 224C67 224 54 218 43 207V221H0Z'
       'M79 109C58 109 42 125 42 148C42 171 58 185 79 185'
       'C101 185 116 170 116 148C116 125 101 109 79 109Z'),
 'l': 'M0 0H43V221H0Z',
 'c': ('M82 70C108 70 129 79 144 96L116 124'
       'C106 114 95 109 82 109C59 109 43 125 43 148'
       'C43 171 59 185 82 185C96 185 108 179 118 168L145 195'
       'C129 214 108 224 81 224C33 224 0 193 0 148'
       'C0 103 34 70 82 70Z'),
 'k': 'M0 0H43V124L87 73H140L84 137L145 221H94L55 164L43 178V221H0Z',
}
LETTER_PLACEMENTS = [('z',0),('a',165),('b',347),('a',528),('l',711),('a',779),('c',956),('k',1121)]
WORD_ORIGIN = (428, 49)
DOT_CENTER = (1734, 244)
DOT_RADIUS = 26


def mark(fill):
    return f'<path fill="{fill}" d="{MARK_PATH}"/>'


def svg(title, viewbox, width, height, body, description=None):
    desc = f'<desc id="description">{description}</desc>\n' if description else ''
    describedby = ' aria-describedby="description"' if description else ''
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" '
            f'width="{width}" height="{height}" role="img" aria-labelledby="title"{describedby}>\n'
            f'<title id="title">{title}</title>\n{desc}{body}\n</svg>\n')


def write(name, value):
    (ROOT / name).write_text(value, encoding='utf-8')


def logo_body(mark_fill, letter_fill, dot_fill):
    letters = ''.join(f'<path transform="translate({x} 0)" d="{GLYPHS[letter]}"/>' for letter,x in LETTER_PLACEMENTS)
    return (mark(mark_fill) + '\n'
            + f'<g transform="translate({WORD_ORIGIN[0]} {WORD_ORIGIN[1]})" fill="{letter_fill}" fill-rule="evenodd">{letters}</g>\n'
            + f'<circle cx="{DOT_CENTER[0]}" cy="{DOT_CENTER[1]}" r="{DOT_RADIUS}" fill="{dot_fill}"/>')


def write_assets():
    for filename, fill, title in [
        ('zabalack-mark.svg', AMBER, 'Zab-al-ack — Ack Relay mark, amber'),
        ('zabalack-mark-dark.svg', IVORY, 'Zab-al-ack — Ack Relay mark, ivory'),
        ('zabalack-mark-mono.svg', NAVY, 'Zab-al-ack — Ack Relay mark, navy'),
    ]:
        write(filename, svg(title, '0 0 372 320', 372, 320, mark(fill),
              'One continuous Z with a rising acknowledgement terminal.'))
    for filename, type_fill, mark_fill, dot_fill, title in [
        ('zabalack-logo-light.svg', NAVY, AMBER, AMBER, 'zabalack. — for light backgrounds'),
        ('zabalack-logo-dark.svg', IVORY, AMBER, AMBER, 'zabalack. — for dark backgrounds'),
        ('zabalack-logo-mono.svg', NAVY, NAVY, NAVY, 'zabalack. — monochrome'),
    ]:
        write(filename, svg(title, '0 0 1760 320', 1760, 320, logo_body(mark_fill, type_fill, dot_fill),
              'The Ack Relay Z symbol beside the outlined lowercase zabalack wordmark and period.'))
    # Use the complete silhouette for icons; no special micro-detail is needed.
    # Warm amber field makes the family recognisable alongside Alrust's rust icon.
    icon_body = (f'<rect width="512" height="512" rx="112" fill="{AMBER}"/>\n'
                 + '<g transform="translate(70 96)">' + mark(NAVY) + '</g>')
    write('zabalack-app-icon.svg', svg('Zab-al-ack application icon', '0 0 512 512', 512, 512, icon_body,
          'A navy Ack Relay Z symbol on an amber rounded square.'))
    favicon_body = (f'<rect width="64" height="64" rx="14" fill="{AMBER}"/>\n'
                    + '<g transform="translate(8.75 12) scale(.125)">' + mark(NAVY) + '</g>')
    write('favicon.svg', svg('Zab-al-ack', '0 0 64 64', 64, 64, favicon_body,
          'The compact navy Ack Relay Z symbol on an amber rounded square.'))
    spec = {
        'name': 'Zab-al-ack',
        'display_wordmark': 'zabalack.',
        'concept': 'Ack Relay',
        'version': '1.0',
        'date': '2026-10-01',
        'status': 'Refined identity; original vector construction.',
        'meaning': 'A single Z route ends in a lifted acknowledgement terminal, reflecting durable delivery from one human action in Alerta to its original scoped Zabbix problem.',
        'palette': {'amber': AMBER, 'navy': NAVY, 'ivory': IVORY, 'white': WHITE},
        'canonical_mark_viewbox': [0, 0, MARK_WIDTH, MARK_HEIGHT],
        'canonical_logo_viewbox': [0, 0, LOGO_WIDTH, LOGO_HEIGHT],
        'construction': {
            'mark': 'One solid path; no detached badge, strokes, gradients or embedded imagery.',
            'corner_radius_units': 8,
            'wordmark': 'Eight outlined lowercase letters and a circular amber period; no runtime font dependency.',
            'wordmark_origin': list(WORD_ORIGIN),
            'mark_to_wordmark_gap_units': 56,
            'top_bar_thickness_units': 64,
            'icon': 'Navy mark on an amber rounded square; the same mark geometry at every size.',
        },
        'clear_space': {
            'minimum_mark_units': 64,
            'rule': 'Keep at least one top-bar thickness (64 mark units) clear on every side of the complete mark or wordmark.',
            'compact_ui_exception': 'Supplied favicon and app icon include their own optical padding; do not add external padding inside their square.'
        },
        'minimum_sizes': {
            'standalone_mark_width_px': 24,
            'full_wordmark_width_px': 176,
            'recommended_desktop_header_width_px': 208,
            'recommended_mobile_header_width_px': 180,
            'favicon_px': 16,
            'guidance': 'At 16–23 px, prefer the supplied favicon composition; reserve the full wordmark for widths of 176 px or more.'
        },
        'variants': {
            'zabalack-mark.svg': 'Amber on ivory or navy.',
            'zabalack-mark-dark.svg': 'Ivory for dark backgrounds or reversed monochrome use.',
            'zabalack-mark-mono.svg': 'Navy for light backgrounds and one-color output.',
            'zabalack-logo-light.svg': 'Amber mark and period with navy letters on ivory or white.',
            'zabalack-logo-dark.svg': 'Amber mark and period with ivory letters on navy.',
            'zabalack-logo-mono.svg': 'All navy, including the period.',
            'favicon.svg': 'Navy mark on amber, optimized padded composition for 16–32 px.',
            'zabalack-app-icon.svg': '512 px master of the same icon composition.'
        },
        'usage': [
            'Preserve the silhouette, clear inner pocket and lifted terminal.',
            'Use the matching logo variant for the background.',
            'Keep the amber period in primary wordmarks; keep it monochrome in the mono version.',
            'Do not stretch, outline, rotate, add gradients, shadows, textures or detached check badges.',
            'The visual wordmark is zabalack.; the official project name remains Zab-al-ack. This identity does not rename a repository, package, API or command.',
            'Do not describe the mark as many-alert aggregation, automatic re-acknowledgement, full synchronization or bidirectional writes.'
        ],
        'license': 'Apache-2.0',
        'rights_note': 'Original, project-owned branding supplied under the project Apache-2.0 license. No trademark registration or affiliation claim is made.',
        'validation': [
            'All SVG lettering is real paths; no fonts or bitmap embeds.',
            'Every mark variant uses the same canonical path.',
            'No scripts, external URLs, filters, masks or clipping dependencies.',
            'Rendered and inspected at 16, 24 and 32 px; header lockups checked at 180 and 208 px.'
        ]
    }
    write('brand-spec.json', json.dumps(spec, indent=2, ensure_ascii=False) + '\n')
    write('BRAND.md', '''# Zab-al-ack — Ack Relay

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
''')


def validate():
    names = ['zabalack-mark.svg','zabalack-mark-dark.svg','zabalack-mark-mono.svg','zabalack-logo-light.svg','zabalack-logo-dark.svg','zabalack-logo-mono.svg','favicon.svg','zabalack-app-icon.svg']
    for name in names:
        text = (ROOT/name).read_text(encoding='utf-8')
        root = ET.fromstring(text)
        tags = {node.tag.rsplit('}', 1)[-1] for node in root.iter()}
        assert not tags & {'text','image','script','foreignObject','style','filter','mask','clipPath'}, (name, tags)
        assert 'href=' not in text and 'url(' not in text, name
        assert MARK_PATH in text, name
        assert root.attrib['viewBox'], name
    print('8 SVG assets written; XML, shared-path and resource checks passed.')


def render():
    from PIL import Image, ImageDraw, ImageFont
    previews = ROOT/'previews'
    previews.mkdir(exist_ok=True)
    def png(name, width, destination=None):
        output = previews/(destination or f'{Path(name).stem}-{width}.png')
        subprocess.run(['inkscape',str(ROOT/name),'--export-type=png',f'--export-width={width}',f'--export-filename={output}'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return output
    for name in ['zabalack-logo-light.svg','zabalack-logo-dark.svg','zabalack-logo-mono.svg']:
        png(name,1760)
    for name in ['zabalack-mark.svg','zabalack-mark-dark.svg','zabalack-mark-mono.svg']:
        png(name,372)
    png('zabalack-app-icon.svg',512)
    for size in [16,24,32]:
        png('zabalack-mark.svg',size,f'mark-{size}.png')
        png('zabalack-mark-mono.svg',size,f'mark-mono-{size}.png')
        png('favicon.svg',size,f'favicon-{size}.png')
    for size in [180,208]:
        png('zabalack-logo-light.svg',size,f'header-{size}.png')
    def font(size):
        for candidate in ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','DejaVuSans.ttf','Arial.ttf']:
            try:
                return ImageFont.truetype(candidate,size)
            except OSError:
                pass
        return ImageFont.load_default()
    def paste(target, path, x, y, max_size=None):
        im = Image.open(path).convert('RGBA')
        if max_size:
            im.thumbnail(max_size,Image.Resampling.LANCZOS)
        target.paste(im,(x,y),im)
        return im
    # Clean ivory reference for image-generation guidance and reuse.
    reference = Image.new('RGB',(2000,800),IVORY)
    paste(reference,previews/'zabalack-logo-light-1760.png',120,235)
    reference.save(previews/'zabalack-brand-reference.png')
    sheet = Image.new('RGB',(1800,1360),IVORY)
    draw = ImageDraw.Draw(sheet)
    draw.text((66,39),'ZABALACK  /  ACK RELAY',font=font(30),fill=NAVY)
    draw.text((66,87),'Refined vector identity · One scoped acknowledgement, delivered.',font=font(18),fill='#69717A')
    paste(sheet,previews/'zabalack-logo-light-1760.png',114,204,(1570,286))
    draw.text((66,525),'PRIMARY  /  IVORY',font=font(15),fill='#69717A')
    draw.rectangle((0,570,1799,952),fill=NAVY)
    paste(sheet,previews/'zabalack-logo-dark-1760.png',195,625,(1410,257))
    draw.text((66,916),'PRIMARY  /  NAVY',font=font(15),fill=IVORY)
    draw.text((66,992),'SMALL-SIZE ICONS',font=font(19),fill=NAVY)
    for i,size in enumerate([16,24,32]):
        x=70+i*124
        paste(sheet,previews/f'favicon-{size}.png',x,1044+(32-size)//2)
        draw.text((x,1092),f'{size} px',font=font(14),fill=NAVY)
        im=Image.open(previews/f'favicon-{size}.png').convert('RGBA')
        im=im.resize((size*4,size*4),Image.Resampling.NEAREST)
        sheet.paste(im,(x,1142),im)
    draw.text((470,992),'MONOCHROME',font=font(19),fill=NAVY)
    paste(sheet,previews/'zabalack-logo-mono-1760.png',470,1080,(925,170))
    paste(sheet,previews/'zabalack-app-icon-512.png',1514,1015,(214,214))
    draw.text((1480,1264),'APPLICATION / AVATAR',font=font(15),fill=NAVY)
    sheet.save(previews/'zabalack-brand-contact-sheet.png')
    # Actual-size bitmap proofs plus nearest-neighbour enlargements reveal pixels.
    tiny=Image.new('RGB',(1600,1140),IVORY)
    draw=ImageDraw.Draw(tiny)
    draw.text((60,34),'ACK RELAY / SMALL-SIZE PROOF',font=font(29),fill=NAVY)
    draw.text((60,85),'Left: actual pixels. Right: 6× nearest-neighbour enlargement.',font=font(17),fill='#69717A')
    for row,size in enumerate([16,24,32]):
        y=170+row*260
        draw.text((60,y),f'{size} px',font=font(22),fill=NAVY)
        for col,(key,label,bg) in enumerate([('mark-mono','Navy silhouette',IVORY),('mark','Amber silhouette',NAVY),('favicon','Application icon',IVORY)]):
            x=210+col*440
            draw.text((x,y-18),label,font=font(17),fill=NAVY)
            draw.rectangle((x-14,y+18,x+396,y+230),fill=bg)
            im=Image.open(previews/f'{key}-{size}.png').convert('RGBA')
            tiny.paste(im,(x+8,y+64),im)
            im=im.resize((im.width*6,im.height*6),Image.Resampling.NEAREST)
            tiny.paste(im,(x+114,y+30),im)
    draw.text((60,993),'HEADER LOCKUPS / ACTUAL SIZE',font=font(18),fill=NAVY)
    for i,size in enumerate([180,208]):
        x=470+i*480
        paste(tiny,previews/f'header-{size}.png',x,997)
        draw.text((x,1060),f'{size} px wide',font=font(15),fill=NAVY)
    tiny.save(previews/'zabalack-tiny-size-proof.png')
    print('Reference, contact sheet and tiny-size proofs written to previews/.')

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render',action='store_true',help='Render PNG previews with local Inkscape and Pillow.')
    args=parser.parse_args()
    write_assets()
    validate()
    if args.render:
        render()
