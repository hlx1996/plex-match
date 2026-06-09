---
name: plex-match
description: "Plex 媒体库批量匹配与验证：扫描 Plex 服务器所有媒体库（电影、电视剧等），找到未匹配（unmatched）的资源并自动匹配元数据，然后验证所有匹配是否正确并自动修正错误。当用户提到 Plex 匹配、unmatched、元数据匹配、Plex 刮削、海报缺失时使用。"
argument-hint: "<plex-url>"
---

# Plex Match

批量匹配 Plex 服务器中所有媒体库的未匹配资源，并验证所有匹配的正确性。

## Workflow

### Phase 1: 获取信息

1. **Plex URL**：如果用户提供了 URL（如 `https://plex.example.com`），直接使用。否则向用户询问。
2. **Token**：告知用户获取方法，等待用户直接粘贴 Token（不要用 AskUserQuestion，直接让用户在对话中输入）：

> 获取 Plex Token 的方法：
> 1. 浏览器打开你的 Plex Web 界面
> 2. 按 `F12` 或 `Cmd+Option+I` 打开开发者工具
> 3. 切换到 **Network** 标签，刷新页面
> 4. 点击任意请求，在 URL 中找 `X-Plex-Token=xxxxx`
>
> 找到后直接把 Token 发给我即可。

等待用户回复 Token 后继续。

### Phase 2: 验证连接

```bash
curl -s "<BASE>/library/sections?X-Plex-Token=<TOKEN>" | head -200
```

确认能获取到媒体库列表。401 则 Token 无效，引导重新获取。

### Phase 3: 批量匹配 Unmatched

```bash
python3 <skill-dir>/scripts/plex_match.py \
  --base "<BASE>" --token "<TOKEN>" --delay 1.0
```

参数：`--delay`（请求间隔）、`--library <key>`（指定库）、`--dry-run`（只列出不匹配）。

匹配量大时用 background 运行，定期检查日志进度。脚本内置 503 重试（3 次，间隔递增）。

匹配完成后，对脚本报告的 failed 项，逐个手动搜索修正：
1. 用 `/library/metadata/<key>/matches` 搜索正确结果
2. 用 `PUT /library/metadata/<key>/match` 应用匹配
3. 如果 Plex 数据库中确实没有该项，记录并告知用户

### Phase 4: 验证所有匹配

匹配完成后 **必须** 运行验证脚本，检查全部媒体（包括原本已匹配的）：

```bash
python3 <skill-dir>/scripts/plex_verify.py \
  --base "<BASE>" --token "<TOKEN>" --fix --delay 0.3
```

脚本会：
1. 遍历所有媒体库，获取每项的文件路径
2. 对比文件名与 Plex 匹配标题的相似度
3. 标记可疑匹配（相似度低于阈值）
4. `--fix` 模式自动搜索正确匹配并修正
5. 报告仍然 unmatched 的项

结果保存到 `/tmp/plex_verify_result.json`。

验证完成后，读取 JSON 结果并向用户汇报：
- 自动修复了多少项
- 仍然 unmatched 的项（提醒用户关注，**不要强行匹配**）
- 需要手动审查的项

### Phase 5: 汇报结果

向用户输出最终报告：
- 各媒体库总数和 unmatched 数
- 本次新匹配数 / 失败数
- 验证发现并修正的错误数
- 仍无法匹配的项（建议用户检查文件名是否规范）
- 提醒刷新 Plex 界面加载新元数据

## 已知陷阱

### 不要盲目取第一个搜索结果
Plex 搜索 API 返回的第一个结果不一定是正确的。常见错误：
- 匹配到同名纪录片 / Making-of（如 "LOTR3" 匹配到 "LOTR Symphony"）
- 匹配到不同年份的同名片（如 "Dumbo 2019" 匹配到 "Mumbo Jumbo 2017"）
- 匹配到完全不同语言的同名片（如 "家有喜事" 匹配到 "Alles Lüge"）

脚本已优化：优先选择同年份结果，跳过纪录片类结果。

### 电视剧季别年份差异是正常的
Plex 按剧集（show）级别匹配，`year` 字段是整部剧的首播年份，不是具体季的年份。如 "咒术回战 S02 (2023)" 匹配到 "咒术回战 (2020)" 是正确的。

### 外语片 originalTitle 不同是正常的
韩语/日语/法语等外语片，Plex 匹配后的中文标题与文件中的外语原名不同是正常行为，不算错误。

### 繁简体差异是正常的
"长江七号" vs "長江七號"、"唐伯虎点秋香" vs "唐伯虎點秋香" 是繁简体差异，匹配正确。

### 合集文件夹会导致验证误报
如果多个电影文件放在同一个合集文件夹里（如 "漫威超级英雄电影22部合集"），文件夹名和单个电影标题自然不匹配。验证脚本会跳过合集文件夹。

### Plex 数据库中不存在的资源无法匹配
部分冷门资源（如 "山东味道"、"世界旦夕之间"）在 Plex 数据库中不存在，无法自动匹配。这些应该报告给用户而非强行匹配到错误的结果。
