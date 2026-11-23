# Fonts served by the web control page

These are third-party font files. They are not covered by the repository's MIT `LICENSE`. Each one stays under its own licence, given below.

## Lato (SIL Open Font License 1.1)

| File | SHA-256 |
| --- | --- |
| `LatoLatin-Regular.woff2` | `ddd4ef7f97f4361b60841d59753218a57134b0f99f5b46a9612234f1c2733ab0` |
| `LatoLatin-Bold.woff2` | `276401632a998400be8a5895038f4f72d3760d3c3d6aaf3cf445d109cb9d1540` |
| `LatoLatin-Black.woff2` | `e2c997abb38ede2240d957b57a3216882e8416b1f757f26b92128a8875e00e73` |

- Source: the copyright holder's web font kit "Lato 2.0 web fonts" (`Lato2OFLWeb.zip`, from http://www.latofonts.com/). The files come from its `LatoLatin/fonts/` folder.
- Version: 2.015 (2015-08-06).
- The files are byte-identical to the kit's. They are the author's own Latin web versions, the Original Version under the OFL, so they keep the Reserved Font Name "Lato".
- Licence: `OFL.txt`. Its copyright line is the one each font file carries in its name table. The kit's own `OFL.txt` header also gives the author's contact address. This public repository carries no personal contact details, so that address is left out here.

## URW Gothic (AGPL-3.0 with the font exception)

| File | SHA-256 |
| --- | --- |
| `URWGothic-Demi.woff2` | `d88cd154be463a64e8a9398a80c7c8c521c237e8b5b5164a7fdc58c061b5b3c4` |

- Source: the (URW)++ base 35 fonts, https://github.com/ArtifexSoftware/urw-base35-fonts, release tag `20200910`, file `fonts/URWGothic-Demi.otf`, font version 1.00, SHA-256 `5b009410cf5231dcb1e45b155c1afedcfc63d82042fd8c414d0dd7705c9fbbae`. That file is the Corresponding Source of this one.
- Copyright: (URW)++, Copyright 2014 by (URW)++ Design & Development.
- Modified on 2026-11-22: the font was cut down to the code points the page uses and converted to WOFF2. The code points are U+0020-007E, U+00A0-00FF, U+2013-2014, U+2018-201D, U+2022, U+2026, U+2192 and U+2212. The outlines, advance widths and layout tables of those glyphs are unchanged. fontTools' `pyftsubset URWGothic-Demi.otf --unicodes=<the list above> --flavor=woff2` makes an equivalent subset from the upstream file.
- Licence: `LICENSE-URW-base35.txt`, which is upstream's `LICENSE` with its font exception, and `COPYING-AGPL-3.0.txt`, which is upstream's `COPYING`.
