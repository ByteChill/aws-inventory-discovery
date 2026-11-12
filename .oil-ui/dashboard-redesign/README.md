# Dashboard 原型

静态原型，正式前端迁移时的设计参照。数据全是假数据，不连后端。

- `inventory.html`：定稿原型，页面和交互都按它做。用浏览器直接打开即可。
- `index.html`、`console.html`、`cards.html`：早期方向探索，只留作设计记录，不参照。
- `*.jpg`、`mole-reference.png`：探索阶段的截图和配色参照图。

本地预览（端口 5184，配置在 `.claude/launch.json`）：

```bash
cd .oil-ui/dashboard-redesign && python3 -m http.server 5184 --bind 127.0.0.1
# http://127.0.0.1:5184/inventory.html
```

规格以 [docs/specs/dashboard.md](../../docs/specs/dashboard.md) 为准；原型和规格不一致时，先改规格并确认，再迁移。
