#!/usr/bin/env python3
"""Write python/src/camouwolf/data/chrome-languages.json from a Chromium tree.

Chrome keeps one default language list per UI language (IDS_ACCEPT_LANGUAGES
in components/strings/components_locale_settings_*.xtb): German is
"de-DE,de,en-US,en", Spanish "es-ES,es". Chromium compiles those lists into
kIncognitoLanguageListMap, and an off-the-record profile (every non-persistent
Playwright context) serves the list keyed by the first language instead of the
user's own. The launcher reads the same table so that the languages it claims
are the ones Chrome would serve.

This runs Chromium's own generator (generate_incognito_language_list_map.py)
and converts its header, so the table is exactly what the pinned build
compiled. Rerun it when the Chromium pin moves:

    scripts/gen-chrome-languages.py <chromium>/src
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / 'python' / 'src' / 'camouwolf' / 'data' / 'chrome-languages.json'
GENERATOR = Path('components/language/core/browser/generate_incognito_language_list_map.py')
GRD = Path('components/components_locale_settings.grd')
# language_prefs.h kFallbackInputMethodLocale: the key whose list is appended
# to a first language the table does not have.
FALLBACK = 'en-US'
ENTRY = re.compile(r'^\s*\{"([^"]+)", "([^"]+)"\},$')


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    with tempfile.TemporaryDirectory() as tmp:
        header = Path(tmp) / 'map.h'
        subprocess.run([sys.executable, str(src / GENERATOR), str(src / GRD), str(header)], check=True)
        lists = {}
        for line in header.read_text().splitlines():
            match = ENTRY.match(line)
            if match:
                lists[match.group(1)] = match.group(2).split(',')
    if FALLBACK not in lists:
        print(f'{FALLBACK} missing from the generated map', file=sys.stderr)
        return 1
    OUT.write_text(json.dumps({'fallback': FALLBACK, 'lists': lists}, indent=1, sort_keys=True) + '\n')
    print(f'{OUT.relative_to(REPO)}: {len(lists)} languages')
    return 0


if __name__ == '__main__':
    sys.exit(main())
