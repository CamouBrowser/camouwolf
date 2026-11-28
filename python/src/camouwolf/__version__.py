"""
Camouwolf version constants.
"""


class CONSTRAINTS:
    """
    The minimum and maximum supported versions of the Camouwolf browser.
    """

    MIN_VERSION = 'alpha.1'
    MAX_VERSION = '1'

    # (playwright_version, required_browser_build): from that Playwright on, the
    # browser must be at least that build. Empty: Camouwolf is driven over CDP,
    # which no Playwright release has yet outgrown for a Camouwolf build.
    PLAYWRIGHT_BROWSER_FLOORS: tuple = ()

    # The browser interface built from this tree. Every browser release
    # declares it in its manifest.json (ci/release.py reads it from here), and
    # this library accepts browsers from MIN_INTERFACE up to it. Raise it when a
    # browser change would break released libraries; raise MIN_INTERFACE when
    # this library can no longer drive older browsers. Releases from before
    # manifests carried the field are interface 1.
    INTERFACE = 2
    MIN_INTERFACE = 1

