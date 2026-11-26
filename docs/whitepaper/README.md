# KiroCrew ON 白皮书

面向开发者、Team、平台管理员与决策者的完整介绍：产品定义、核心概念、设计原则、系统架构、端到端使用流程（附截图）、质量与可靠性、协作、安全、管理、部署、适用场景、真实案例、运行数据、限制、路线图与整体解读。

| 文件 | 内容 |
|---|---|
| `WHITEPAPER.md` | **唯一的源文件**，改内容只改它 |
| `index.html` | 网页版：左侧目录、右侧正文，直接用浏览器打开；打印时按 A4 分页。由 `WHITEPAPER.md` 生成 |
| `KiroCrew-ON-白皮书.pdf` | PDF 版，由 `index.html` 打印生成 |
| `img/` | 截图（WebP），来自线上实例 https://kirocrew-on.tinyboat.blog，2026-11-17 与 11-19 截取 |
| `figures/` | 网页版的架构图、会话流程图与集群版示意图（Markdown 版用 mermaid） |
| `video/` | 根据白皮书做的宣传片（约 105 秒，中文配音与字幕）及其脚本与渲染工具，见 [`video/README.md`](video/README.md) |

## 重新生成

在仓库根目录执行。网页版：

```bash
.venv/bin/python tools/md_to_html.py docs/whitepaper/WHITEPAPER.md docs/whitepaper/index.html --book
```

PDF：PDF 里放不了 WebP，Chrome 会把截图按无损存进去（约 15 MB），所以先在临时副本里把截图转成 JPEG 再打印（约 9 MB，53 页）：

```bash
T=$(mktemp -d); cp -R docs/whitepaper/figures "$T/"; mkdir "$T/img"
for f in docs/whitepaper/img/*.webp; do sips -s format jpeg -s formatOptions 80 "$f" --out "$T/img/$(basename "${f%.webp}").jpg" >/dev/null; done
sed 's/\.webp"/.jpg"/g' docs/whitepaper/index.html > "$T/index.html"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --no-pdf-header-footer --virtual-time-budget=20000 \
  --print-to-pdf="docs/whitepaper/KiroCrew-ON-白皮书.pdf" "file://$T/index.html"
rm -rf "$T"
```

## 来历

截图与使用流程、适用场景、案例、运行数据最初整理于 kirocrew-octopus 仓库的 `docs/whitepaper/`（2026-11-19）。本目录在此基础上合并了设计原则、恢复阶梯、协作与安全模型、集群版、各章「解读」与整体解读，并按 2026-11-20 的代码订正。

## 分享前注意

截图包含内部信息：会话标题与需求、用户名、Token 名称、执行环境名称。对外发布前请确认可以公开。
