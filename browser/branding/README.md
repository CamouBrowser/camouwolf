# browser/branding

**Generated. Do not hand-edit.** Every file here is rendered from
`browser/assets/CamouwolfLogo.svg` by:

```bash
python3 browser/scripts/generate-branding.py
```

This tree mirrors Chromium's `chrome/app/theme/` layout one-for-one.
`browser/scripts/apply-patches.sh` overlays it onto the checkout before the
build, so each file lands on the upstream asset it replaces.

## What reaches a Linux `is_chrome_branded=false` build

Verified against the pinned tag's `theme_resources.grd`,
`chrome_unscaled_resources.grd` and `installer/linux/BUILD.gn` — not from
memory, because the resolution rules are not what they look like:

| Path | Resource | Where it shows up |
| --- | --- | --- |
| `default_100_percent/chromium/linux/product_logo_16.png` | `IDR_PRODUCT_LOGO_16` | tab strip, small window decorations |
| `default_100_percent/chromium/product_logo_32.png` | `IDR_PRODUCT_LOGO_32` | window icon, task switcher |
| `default_200_percent/chromium/…` | the same, at 2x | HiDPI |
| `chromium/linux/product_logo_{64,128,256}.png` | `IDR_PRODUCT_LOGO_{64,128,256}` | `_NET_WM_ICON`, about pages, shortcut art |
| `chromium/product_logo.svg` | `IDR_PRODUCT_LOGO_SVG` | `chrome://` UI that wants a vector |
| `chromium/product_logo_animation.svg` | `IDR_PRODUCT_LOGO_ANIMATION_SVG` | replaced by a still mark — the original is the Chromium ball's spin, which has no Camouwolf equivalent |

Two traps worth writing down:

- **`chrome_scaled_image` entries in `theme_resources.grd` resolve under
  `default_{100,200}_percent/`, not under `chromium/`.** That is why upstream's
  `chrome/app/theme/chromium/` contains no `product_logo_32.png`, and why
  putting one there would do exactly nothing.
- **Linux takes the `chromium/linux/` variants** for 16/24/48/64/128/256. The
  non-Linux paths are kept in sync anyway so a future macOS or Windows target
  does not ship a half-rebranded tree.

## Carried for the other targets

`chromium/win/chromium.ico`, `chromium/mac/app.icns` and
`chromium/linux/product_logo_32.xpm` are used, if at all, only by a build for
their own OS (browser/args/); they exist so that a Windows or macOS build does
not ship Chromium's icon. The macOS `Assets.car` / `AppIcon.icon`
bundle is **not** generated — it needs Xcode's `actool` — so a real macOS
release still needs that step added.

## Deliberately absent

`product_logo_name_22.png` and `product_logo_name_22_white.png` — the horizontal
"Chromium" *wordmark*, not the mark. A portrait silhouette scaled into a 22px
name plate is unreadable, and on Linux nothing references them.
