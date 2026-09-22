"""One-time public logo/icon download; never runs when opening the dashboard."""
from pathlib import Path
import hashlib
import json
import requests
from nba_api.stats.static.teams import get_teams

ROOT = Path(__file__).parent / 'static'


def main():
    aliases = {'NOP': 'no', 'NYK': 'ny', 'GSW': 'gs', 'SAS': 'sa', 'UTA': 'utah'}
    assets = [(f'assets/{t["abbreviation"]}.png', 'https://a.espncdn.com/i/teamlogos/nba/500/' + aliases.get(t['abbreviation'], t['abbreviation'].lower()) + '.png') for t in get_teams()]
    assets += [('vendor/lucide.js','https://unpkg.com/lucide@0.468.0/dist/umd/lucide.min.js'),
               ('vendor/lucide-LICENSE','https://unpkg.com/lucide@0.468.0/LICENSE')]
    manifest = []
    for relative, url in assets:
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            response = requests.get(url, timeout=40)
            response.raise_for_status()
            if relative.endswith('.png') and not response.content.startswith(b'\x89PNG'):
                raise ValueError('Invalid PNG response')
            path.write_bytes(response.content)
        manifest.append(dict(path=relative, url=url, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (ROOT / 'assets/manifest.json').write_text(json.dumps(manifest, indent=2))
    print(f'{len(manifest)} local assets ready. NBA team marks belong to their respective owners.')


if __name__ == '__main__':
    main()
