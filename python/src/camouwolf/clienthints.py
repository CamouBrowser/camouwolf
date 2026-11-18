"""User-Agent Client Hints: the surface Firefox does not have.

A Chrome identity is a set of values that must agree:

    navigator.userAgent            "...Windows NT 10.0; Win64; x64...Chrome/158.0.0.0..."
    navigator.userAgentData.brands [{Chromium 158}, {Google Chrome 158}, {GREASE}]
    Sec-CH-UA                      the same brand list, on every request
    Sec-CH-UA-Platform             "Windows"
    Sec-CH-UA-Mobile               ?0
    Sec-CH-UA-Arch / -Bitness / -Platform-Version / -Full-Version-List (high entropy)

The low-entropy hints go out on every request with no opt-in, so a server sees
them before any script runs. `--user-agent` moves the UA string and not the
hints, which is why camouwolf.switches refuses it and everything here is derived
from one identity. The keys are read by browser/patches/0003-user-agent-client-hints.patch.

Ported from Kraft's src/clienthints.ts.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

OsKey = str  # 'win' | 'mac' | 'lin'

# Sec-CH-UA-Platform values.
CH_PLATFORM: Dict[str, str] = {'win': 'Windows', 'mac': 'macOS', 'lin': 'Linux'}

# navigator.platform values, which are not the Sec-CH-UA-Platform ones.
NAV_PLATFORM: Dict[str, str] = {'win': 'Win32', 'mac': 'MacIntel', 'lin': 'Linux x86_64'}

# The parenthesised part of the UA string. Frozen by Chrome's UA reduction since
# M100: macOS stays "10_15_7", Windows "10.0; Win64; x64" from 10 through 11,
# Linux "X11; Linux x86_64". The real OS version is in Sec-CH-UA-Platform-Version.
UA_PLATFORM_TOKEN: Dict[str, str] = {
    'win': 'Windows NT 10.0; Win64; x64',
    'mac': 'Macintosh; Intel Mac OS X 10_15_7',
    'lin': 'X11; Linux x86_64',
}

# Sec-CH-UA-Platform-Version values Chrome reports, per OS. assert_coherent
# refuses any other.
#   win: the UniversalApiContract ladder, not the marketing version: every
#        Windows 10 reports 10.0.0, Windows 11 21H2/22H2 13.0.0/14.0.0/15.0.0,
#        and 19.0.0 was measured on build 26200. 16-18 have not been seen on a
#        shipping build, so they are absent.
#   mac: macOS Sonoma and Sequoia point releases.
#   lin: the kernel version.
PLATFORM_VERSIONS: Dict[str, List[str]] = {
    'win': ['10.0.0', '13.0.0', '14.0.0', '15.0.0', '19.0.0'],
    'mac': ['14.5.0', '14.6.1', '15.1.0', '15.2.0', '15.3.1'],
    'lin': ['6.8.0', '6.11.0', '6.12.0'],
}

# The version a generated identity claims when nothing names one.
DEFAULT_PLATFORM_VERSION: Dict[str, str] = {'win': '15.0.0', 'mac': '15.3.1', 'lin': '6.12.0'}

_CHROME_VERSION = re.compile(r'Chrome/\d+(\.\d+)*')


@dataclass
class ChromeIdentity:
    milestone: int
    full_version: str
    os: OsKey
    platform_version: Optional[str] = None
    arch: Optional[str] = None  # 'x86' | 'arm'
    bitness: str = '64'
    model: str = ''
    wow64: bool = False
    mobile: bool = False


def ua_os(user_agent: str) -> Optional[OsKey]:
    """The OS a user agent claims, or None."""
    if 'Windows NT' in user_agent:
        return 'win'
    if 'Mac OS X' in user_agent:
        return 'mac'
    if re.search(r'Linux|X11|CrOS', user_agent):
        return 'lin'
    return None


def build_user_agent(identity: ChromeIdentity) -> str:
    return (
        f'Mozilla/5.0 ({UA_PLATFORM_TOKEN[identity.os]}) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        f'Chrome/{identity.milestone}.0.0.0 Safari/537.36'
    )


def retarget_user_agent(user_agent: str, milestone: Any) -> str:
    """The user agent with its Chrome version set to `milestone`.0.0.0.

    fpgen's fingerprints were recorded on whatever Chrome the machine ran; a UA
    behind the binary disagrees with the brand list the browser builds.
    """
    return _CHROME_VERSION.sub(f'Chrome/{int(milestone)}.0.0.0', user_agent)


def grease_brand(milestone: int) -> Dict[str, str]:
    """The GREASE entry of the brand list for a milestone.

    Chrome puts one odd brand in every list; a list without one is not Chrome's.
    It is a function of the milestone, so two launches of one milestone agree.
    """
    separators = [
        ['Not', 'A', 'Brand'],
        ['Not_A', 'Brand'],
        ['Not.A', 'Brand'],
        ['Not-A', 'Brand'],
        ['Not(A', 'Brand'],
        ['Not:A', 'Brand'],
        ['Not;A', 'Brand'],
        ['Not=A', 'Brand'],
        ['Not?A', 'Brand'],
    ]
    versions = ['8', '24', '99']
    pick = separators[milestone % len(separators)]
    brand = f'{pick[0]}{pick[1]};{pick[2]}' if len(pick) == 3 else f'{pick[0]};{pick[1]}'
    return {'brand': brand, 'version': versions[milestone % len(versions)]}


def build_brand_list(identity: ChromeIdentity, full: bool = False) -> List[Dict[str, str]]:
    """The Sec-CH-UA / navigator.userAgentData.brands list; `full` gives the
    Sec-CH-UA-Full-Version-List form."""
    version = identity.full_version if full else str(identity.milestone)
    grease = grease_brand(identity.milestone)
    brands = [
        {'brand': 'Chromium', 'version': version},
        {'brand': 'Google Chrome', 'version': version},
        {'brand': grease['brand'], 'version': f"{grease['version']}.0.0.0" if full else grease['version']},
    ]
    order = [[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]][identity.milestone % 6]
    return [brands[i] for i in order]


def serialize_brand_list(brands: List[Dict[str, str]]) -> str:
    """A brand list as the Sec-CH-UA header carries it."""
    return ', '.join(f'"{b["brand"]}";v="{b["version"]}"' for b in brands)


def client_hint_config(identity: ChromeIdentity) -> Dict[str, Any]:
    """Every client-hint config key for one identity."""
    return {
        'navigator.userAgent': build_user_agent(identity),
        'navigator.platform': NAV_PLATFORM[identity.os],
        'userAgentData.platform': CH_PLATFORM[identity.os],
        'userAgentData.platformVersion': identity.platform_version or DEFAULT_PLATFORM_VERSION[identity.os],
        'userAgentData.architecture': identity.arch or ('arm' if identity.os == 'mac' else 'x86'),
        'userAgentData.bitness': identity.bitness,
        # Desktop Chrome sends an empty model; a populated one means Android.
        'userAgentData.model': identity.model,
        'userAgentData.uaFullVersion': identity.full_version,
        # 32-bit Chrome on 64-bit Windows; false whenever bitness is 64.
        'userAgentData.wow64': identity.wow64,
        'userAgentData.mobile': identity.mobile,
    }


def fill_client_hints(config: Dict[str, Any], target_os: OsKey, full_version: str) -> None:
    """Set the client hints the identity's user agent implies, keeping any the
    config already holds."""
    identity = ChromeIdentity(
        milestone=int(full_version.split('.', 1)[0]), full_version=full_version, os=target_os
    )
    for key, value in client_hint_config(identity).items():
        if key not in ('navigator.userAgent', 'navigator.platform'):
            config.setdefault(key, value)
    # The brand lists (Sec-CH-UA, Sec-CH-UA-Full-Version-List and
    # navigator.userAgentData). Without them the browser sends its own list,
    # which has no "Google Chrome" brand.
    config.setdefault('userAgentData.brands', build_brand_list(identity))
    config.setdefault('userAgentData.fullVersionList', build_brand_list(identity, full=True))


def assert_coherent(config: Dict[str, Any]) -> List[str]:
    """The ways this config's user agent and client hints contradict each other.

    Run on every launch: the hints are derived from one identity, but a caller
    can set any of them through `config`. Empty means coherent.
    """
    problems: List[str] = []
    ua = config.get('navigator.userAgent')
    if not ua or not isinstance(ua, str):
        return problems
    ch_platform = config.get('userAgentData.platform')
    nav_platform = config.get('navigator.platform')
    os_key = ua_os(ua)

    if os_key and ch_platform and CH_PLATFORM[os_key] != ch_platform:
        problems.append(
            f'navigator.userAgent claims {CH_PLATFORM[os_key]} but Sec-CH-UA-Platform says '
            f'"{ch_platform}"; every request carries this hint.'
        )
    if os_key and nav_platform and NAV_PLATFORM[os_key] != nav_platform:
        problems.append(
            f'navigator.userAgent claims {CH_PLATFORM[os_key]} but navigator.platform is '
            f'"{nav_platform}" (expected "{NAV_PLATFORM[os_key]}").'
        )

    platform_version = config.get('userAgentData.platformVersion')
    if os_key and platform_version and platform_version not in PLATFORM_VERSIONS[os_key]:
        problems.append(
            f'Sec-CH-UA-Platform-Version "{platform_version}" is not a value Chrome reports on '
            f'{CH_PLATFORM[os_key]} (one of {", ".join(PLATFORM_VERSIONS[os_key])}). On Windows '
            'this is the UA-CH ladder, not the marketing version.'
        )

    if config.get('userAgentData.mobile') is True and 'Mobile' not in ua:
        problems.append("Sec-CH-UA-Mobile is ?1 but the user agent has no 'Mobile' token.")

    if config.get('userAgentData.wow64') is True and config.get('userAgentData.bitness') == '64':
        problems.append(
            'Sec-CH-UA-WoW64 is ?1 with 64-bit bitness; WoW64 is a 32-bit Chrome on 64-bit '
            'Windows, so bitness must be "32".'
        )

    model = config.get('userAgentData.model')
    if model and not re.search(r'Android|Mobile', ua):
        problems.append(f'Sec-CH-UA-Model is "{model}" on a desktop user agent; desktop Chrome sends "".')

    full_version = config.get('userAgentData.uaFullVersion')
    ua_major = re.search(r'Chrome/(\d+)', ua)
    if full_version and ua_major and str(full_version).split('.', 1)[0] != ua_major.group(1):
        problems.append(
            f'navigator.userAgent says Chrome/{ua_major.group(1)} but '
            f'Sec-CH-UA-Full-Version-List says {full_version}.'
        )

    return problems
