# Accent palettes

One accent color per page, taken from traditional Chinese colors. Switch with
`?palette=<id>` (remembered in localStorage). Default: `dai-lv`.

Neutrals (warm grays) are shared by all palettes:

| Token        | Light                           | Dark               |
| ------------ | ------------------------------- | ------------------ |
| background   | `#FAF8F5`                       | `#1B1917`          |
| surface      | `#FFFEFB`                       | `#232120`          |
| text         | `#2A2622` (14.2:1)              | `#ECE7E0` (14.3:1) |
| muted text   | `#6B645C` (5.5:1 on background) | `#A39B91` (6.4:1)  |
| input border | `#968E83` (3.1:1)               | `#7C746A` (3.8:1)  |

| id        | Name | Light accent | on background / on white | Dark accent | on background / on surface | Text on accent (dark button) |
| --------- | ---- | ------------ | ------------------------ | ----------- | -------------------------- | ---------------------------- |
| `dai-lv`  | 黛绿 | `#2E5E52`    | 7.0 / 7.4                | `#8CC4B3`   | 8.9 / 8.1                  | `#12201C` 8.5                |
| `dai-lan` | 黛蓝 | `#3D5873`    | 7.0 / 7.4                | `#9DB9D6`   | 8.6 / 7.9                  | `#10202E` 8.2                |
| `yan-zhi` | 胭脂 | `#8F2B35`    | 7.7 / 8.2                | `#E59AA0`   | 7.9 / 7.2                  | `#2E0E11` 8.0                |

In light mode the button text is white (`#FFFFFF`) on the accent; the ratios
in the "on white" column apply. All pairs meet WCAG AA (4.5:1) for text.

Status colors (small text and badges only):

| Token                           | Light (text on soft bg) | Dark                         |
| ------------------------------- | ----------------------- | ---------------------------- |
| warn (`#6B5410` on `#F3EBCF`)   | 6.1:1                   | `#DCC274` on `#322D1B` 7.9:1 |
| danger (`#9B2C2C` on `#F6E4E1`) | 6.1:1                   | `#E58E8E` on `#3A2220`       |
