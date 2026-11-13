# Accent palettes

The final product has one accent: **胭脂** (yan-zhi), on warm grays. There is no
palette switch and no `?palette=` parameter. 黛绿 and 黛蓝 are kept here only as
design history.

Neutrals (warm grays) are shared by every theme:

| Token        | Light                           | Dark               |
| ------------ | ------------------------------- | ------------------ |
| background   | `#F5F3F0`                       | `#1B1917`          |
| surface      | `#FFFFFF`                       | `#232120`          |
| text         | `#292724` (13.9:1)              | `#ECE7E0` (14.3:1) |
| muted text   | `#716B66` (4.5:1 on background) | `#A39B91` (6.4:1)  |
| input border | `#968E83` (3.1:1)               | `#7C746A` (3.8:1)  |

| id        | Name | Light accent | on background / on white | Dark accent | on background / on surface | Text on accent (dark button) |
| --------- | ---- | ------------ | ------------------------ | ----------- | -------------------------- | ---------------------------- |
| `yan-zhi` | 胭脂 | `#B52C62`    | 5.1 / 6.0                | `#E59AA0`   | 7.9 / 7.2                  | `#2E0E11` 8.0                |
| `dai-lv`  | 黛绿 | `#2E5E52`    | 7.0 / 7.4                | `#8CC4B3`   | 8.9 / 8.1                  | `#12201C` 8.5                |
| `dai-lan` | 黛蓝 | `#3D5873`    | 7.0 / 7.4                | `#9DB9D6`   | 8.6 / 7.9                  | `#10202E` 8.2                |

In light mode the button text is white (`#FFFFFF`) on the accent; the ratios
in the "on white" column apply. All pairs meet WCAG AA (4.5:1) for text.

Status colors (small text and badges only):

| Token                           | Light (text on soft bg) | Dark                         |
| ------------------------------- | ----------------------- | ---------------------------- |
| warn (`#8A5A00` on `#FBF4E3`)   | 5.6:1                   | `#DCC274` on `#322D1B` 7.9:1 |
| danger (`#9B2C2C` on `#F6E4E1`) | 6.1:1                   | `#E58E8E` on `#3A2220`       |
