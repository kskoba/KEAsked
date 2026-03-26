# App Icons

electron-builder expects app icons in this directory:

- `icon.ico`  — Windows (256×256 recommended, multi-resolution ICO)
- `icon.icns` — macOS (1024×1024 source, will generate all sizes)
- `icon.png`  — Linux (512×512)

## Quick way to generate icons

1. Start with a 1024×1024 PNG of your logo
2. Use one of:
   - https://www.icoconverter.com  (ICO for Windows)
   - `iconutil` on macOS to convert an .iconset folder to .icns
   - `electron-icon-builder` npm package: `npx electron-icon-builder --input=logo.png --output=./`

If no icons are provided, electron-builder will use its built-in default Electron icon.
