---
name: plex-match
description: "Plex 媒体库全库扫描、批量匹配与验证：先强制 scan 全部媒体库，再找到未匹配（unmatched）的资源并自动匹配元数据，然后验证全部匹配是否正确并自动修正错误，最后输出本次新 match 和新 update 的媒体列表。当用户提到 Plex 匹配、unmatched、元数据匹配、Plex 刮削、海报缺失时使用。"
argument-hint: "<plex-url>"
---

# Plex Match

先强制扫描整个 Plex 媒体库，再批量匹配所有未匹配资源，并验证全部匹配的正确性。

## 路径与环境变量约定

`scripts/` 下的所有脚本都通过 `scripts/_config.py` 读环境，**不硬编码任何机器专有值**：

| 变量 | 含义 | 必需 |
| --- | --- | --- |
| `PLEX_BASE` | PMS 地址，如 `http://nas:32400` | 走 REST 的脚本必需 |
| `PLEX_TOKEN` | `X-Plex-Token` 的值 | 二选一 |
| `PLEX_TOKEN_FILE` | 存 token 的文件，默认 `/tmp/plex_token` | 二选一 |
| `PLEX_DB` | `com.plexapp.plugins.library.db` 的路径 | 可选，不设则自动发现 |
| `PLEX_BACKUP_DIR` | 回退映射 JSON 的写出目录，默认 `./plex-match-backups` | 可选 |
| `PLEX_MOVIES_DIR` / `PLEX_TV_DIR` / `PLEX_ANIME_DIR` | 各库根目录，仅一次性修复脚本用 | 按需 |

```bash
export PLEX_BASE="http://nas:32400"
export PLEX_TOKEN="xxxxxxxxxxxx"
python3 <skill-dir>/scripts/plex_snapshot.py /tmp/snap.json
```

`_config.db()` 的自动发现会遍历 `/share/*/.qpkg/PlexMediaServer` 与常见的
`/var/lib/plexmediaserver/...`，并**按体积过滤掉 QPKG 自带的那个 ~300KB 空壳库**
（`Resources/com.plexapp.plugins.library.db`），只认 100MB+ 的真库。

⚠️ **本文档后半部分的实操复盘里出现的 `/share/CACHEDEV*`、`/share/*/.qpkg/Apache84`
等绝对路径，全部是某一台 QNAP 上的实例值，仅用于说明当时发生了什么，不是要照抄的配置。**
换机器时以本节的环境变量为准。

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

⚠️ 如果 token 看起来是正确的但 API 返回 401，要特别检查是否混入了**形似英文字母的非 ASCII 字符**（例如西里尔字母 `у` 看起来像英文字母 `y`）。Plex token 应该是纯 ASCII。

### Phase 2: 验证连接

```bash
curl -s "<BASE>/library/sections?X-Plex-Token=<TOKEN>" | head -200
```

确认能获取到媒体库列表。401 则 Token 无效，引导重新获取。

### Phase 3: 先强制 scan 全部媒体库

先定位 **Python 3**。不要回退到系统自带的 `python`，因为某些 NAS 环境里的 `python` 实际上还是 Python 2，会导致脚本无法运行。实机可用的兜底路径示例：

```bash
# 备选：QNAP 上 QPKG 自带的 python3（路径按机器改，例如 Apache84 / Entware）
FALLBACK_PY="${PLEX_FALLBACK_PYTHON:-}"
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif [ -n "$FALLBACK_PY" ] && [ -x "$FALLBACK_PY" ]; then
  export LD_LIBRARY_PATH="$(dirname "$FALLBACK_PY")/../lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  PYTHON_BIN="$FALLBACK_PY"
else
  echo "Python 3 is required for plex-match scripts" >&2
  exit 1
fi
```

然后先触发全库扫描，并等待所有库的 `refreshing` 状态回到 `0`：

```bash
"$PYTHON_BIN" <skill-dir>/scripts/plex_scan.py \
  --base "<BASE>" --token "<TOKEN>"
```

扫描结果会保存到 `/tmp/plex_scan_result.json`。只有在扫描完成后，才能进入后续匹配步骤。

### Phase 4: 批量匹配 Unmatched

```bash
"$PYTHON_BIN" <skill-dir>/scripts/plex_match.py \
  --base "<BASE>" --token "<TOKEN>" --delay 1.0
```

参数：`--delay`（请求间隔）、`--library <key>`（指定库）、`--dry-run`（只列出不匹配）、`--result-file`（指定 JSON 结果路径）。

匹配量大时用 background 运行，定期检查日志进度。脚本内置 503 重试（3 次，间隔递增）。

脚本会把结果保存到 `/tmp/plex_match_result.json`，其中：
- `matched_items` = 本次新 match 的媒体列表
- `failed_items` = 本次仍未成功匹配的媒体列表

匹配完成后，对脚本报告的 failed 项，逐个手动搜索修正：
1. 用 `/library/metadata/<key>/matches` 搜索正确结果
2. 用 `PUT /library/metadata/<key>/match` 应用匹配
3. 如果 Plex 数据库中确实没有该项，记录并告知用户

### Phase 5: 验证所有匹配

匹配完成后 **必须** 运行验证脚本，检查全部媒体（包括原本已匹配的）：

```bash
"$PYTHON_BIN" <skill-dir>/scripts/plex_verify.py \
  --base "<BASE>" --token "<TOKEN>" --fix --delay 0.3
```

脚本会：
1. 遍历所有媒体库，获取每项的文件路径
2. 对电影，读取 `Media/Part` 文件路径；对电视剧/动漫/纪录片，额外读取 show 级别的 `Location` 目录路径
3. 对比清洗后的来源路径与 Plex 的 `title` / `originalTitle` / `slug` 相似度，尽量减少“英文文件名 + 中文标题”的误报
4. 将“高置信度错配”和“翻译/别名待人工复核”区分开，不要把所有低相似度项目都直接强修
5. `--fix` 模式只自动修复高置信度错误；其余项目进入人工复核清单
6. 修完后必须按**原始来源路径**回查归属，确认该目录/文件已经挂到正确条目下

结果保存到 `/tmp/plex_verify_result.json`。其中：
- `updated` / `fixed` = 本次新 update（修正）列表
- `unmatched_remaining` = 仍未匹配的项目
- `unfixable` = 需要人工审查的项目

验证完成后，读取 JSON 结果并向用户汇报：
- 自动修复了多少项
- 仍然 unmatched 的项（提醒用户关注，**不要强行匹配**）
- 需要手动审查的项

### Phase 5.5: 用户反馈后的定向 rematch

如果用户明确指出了“当前错误标题 → 正确目标标题”，把用户反馈视为**高优先级事实**，直接做定向 rematch，不要继续依赖相似度猜测。

步骤：
1. 先用 `/library/metadata/<ratingKey>/matches` 按**目标标题**拉候选
2. 选择与用户目标一致的 `guid`
3. 调用 `PUT /library/metadata/<ratingKey>/match`
4. **不要只看旧 `ratingKey` 是否还在**；对于电视剧/动漫，match 到已存在 show 后，旧条目可能会并入目标 show，旧 `ratingKey` 会消失
5. 最后按**原始目录路径**回查，确认该路径现在归属于正确作品

这一条是本次实操的重要结论：对 show 来说，“路径归属正确”比“旧 `ratingKey` 还存在”更能证明修正成功。

### Phase 7: Chinese Localization for Plex（必做）

匹配、验证和必要的定向 rematch 完成后，**必须** 运行 CLP 对媒体库进行拼音排序和标签汉化。

1. 克隆仓库并安装依赖：

```bash
cd /tmp && git clone https://github.com/x1ao4/chinese-localization-for-plex.git
cd chinese-localization-for-plex
pip3 install -r requirements.txt
```

如果当前环境的 `/tmp` 很小、`pip3`/`python3` 不可用，**不要** 在 `/tmp` 里建 venv 硬装。优先复用 Phase 3 已定位出的 `PYTHON_BIN`，并把依赖安装到宿主机持久目录（例如 `/share/CACHEDEV1_DATA/.qoder/tmp/plex-phase78/pydeps`）：

```bash
PHASE78_DIR="${PLEX_WORK_DIR:-$HOME/.plex-match}/pydeps"
mkdir -p "$PHASE78_DIR"
"$PYTHON_BIN" -m pip install --target "$PHASE78_DIR" flask pypinyin requests
PYTHONPATH="$PHASE78_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" chinese-localization-for-plex.py --all
```

2. 配置 `config/config.ini`：

```ini
[server]
address = <BASE>
token = <TOKEN>
skip_libraries =
```

3. 运行处理所有项目：

```bash
python3 chinese-localization-for-plex.py --all
```

脚本会将所有中文标题的排序字段改为拼音首字母缩写，并将英文标签汉化。已处理的项目会被跳过。

### Phase 8: Plex-Trakt 同步 (PlexTraktSync)（必做）

中文本地化完成后，**必须** 运行 PlexTraktSync，将 Plex 与 Trakt 的观看记录、评分、收藏/想看等状态同步。

1. 安装：

```bash
pipx install PlexTraktSync
```

如果 `pipx` 不可用，或者 `/tmp` / 默认缓存目录空间太小导致安装失败，改用宿主机持久目录安装并显式指定 `PTS_CONFIG_DIR` / `PYTHONPATH`：

```bash
PHASE78_DIR="${PLEX_WORK_DIR:-$HOME/.plex-match}/pydeps"
mkdir -p "$PHASE78_DIR"
"$PYTHON_BIN" -m pip install --target "$PHASE78_DIR" PlexTraktSync
export PTS_CONFIG_DIR="$PHASE78_DIR"
export PYTHONPATH="$PHASE78_DIR${PYTHONPATH:+:$PYTHONPATH}"
```

2. 先把 Plex 服务器写进 `servers.yml`，至少包含：

```yaml
servers:
  default:
    token: <TOKEN>
    urls:
      - <BASE>
```

3. 完成 Trakt 登录（首次需要交互式授权）：

```bash
plextraktsync trakt-login
```

如果环境里已经有可用的 `.pytrakt.json` / `.env` / `servers.yml`，直接复用；否则就必须现场补齐并完成授权。首次登录至少需要：
- Trakt API Client ID 和 Client Secret（从 https://trakt.tv/oauth/applications/new 创建）
- Plex 服务器 URL 和 Token（复用 Phase 1 获取的信息）

对于无头/NAS 环境，如果 `trakt-login` / `login` 因 `RemoteDisconnected`、`requests-cache`、TLS 或其他网络异常失败，**不要卡住重试同一条命令**；改用本 skill 自带的手动 device-auth 兜底脚本：

```bash
"$PYTHON_BIN" <skill-dir>/scripts/plex_trakt_device_login.py start \
  --client-id "<TRAKT_CLIENT_ID>" \
  --state-file /tmp/plex_trakt_device_auth.json
```

读取输出里的 `user_code` 和 `verification_url`，让用户完成授权后，再执行：

```bash
"$PYTHON_BIN" <skill-dir>/scripts/plex_trakt_device_login.py finish \
  --client-id "<TRAKT_CLIENT_ID>" \
  --client-secret "<TRAKT_CLIENT_SECRET>" \
  --state-file /tmp/plex_trakt_device_auth.json \
  --config-dir "$PTS_CONFIG_DIR" \
  --plex-server-name default
```

这一步会把 Trakt token 写入 `.pytrakt.json`，并把 `TRAKT_USERNAME` / `PLEX_SERVER` 写入 `.env`，供 PlexTraktSync 直接复用。

4. 运行同步：

```bash
plextraktsync sync
```

如果同步过程中卡在 `metadata.provider.plex.tv`、Plex Online watchlist 或 liked lists 超时，不要因此放弃整个 phase 8。先把以下开关临时关闭，至少完成 **watched / rating** 的核心同步：

```yaml
sync:
  plex_to_trakt:
    watchlist: false
  trakt_to_plex:
    liked_lists: false
    watchlist: false
```

同步完成后，把成功同步的核心结果并入最终报告；如果 watchlist / liked lists 因 Plex Online 超时被跳过，也要明确告知用户。

### Phase 6: 最终输出 & 修正

向用户输出最终报告：
- 各媒体库总数和 unmatched 数
- 本次新匹配数 / 失败数
- 验证发现并修正的错误数
- 用户反馈后的**定向 rematch 列表**（Phase 5.5；如果有手动改正，必须单独列出，不要被 `updated=[]` 掩盖）
- 中文本地化执行结果（phase 7）
- PlexTraktSync 执行结果（phase 8）
- **新 match 列表**（读取 `/tmp/plex_match_result.json` 的 `matched_items`）
- **新 update 列表**（读取 `/tmp/plex_verify_result.json` 的 `updated`）
- 仍无法匹配的项（建议用户检查文件名是否规范）
- 需要人工修正但暂不应强改的项
- 提醒刷新 Plex 界面加载新元数据

如果 `matched_items` 或 `updated` 为空，要明确输出“无”，不要省略这一节。

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

### 搜索时要用多种关键词尝试
同一个资源可能有中文名、英文名、原名（日韩德法等）。搜索匹配时如果第一个关键词没有结果，要换其他关键词重试：
- 中文标题（`山东味道`）
- 英文标题（`A Bite of Shan Dong`）
- 拼音/罗马字（`Shan Dong Wei Dao`）
- 原名（`Welt am Draht`）

不要因为一次搜索没结果就认定不存在。验证脚本对 failed 项也应多试几个关键词。**对中文内容优先用中文名搜索**——英文名可能返回错误的同名资源（如搜 "Black Mirror" 返回 "来自未来的故事"，搜 "黑镜" 才返回正确结果）。

### Plex Discover 目录中的资源可能搜不到
部分资源存在于 Plex Discover 目录中，但不会出现在常规 `/matches` 搜索结果里。如果搜索多次都没有结果：
1. 让用户在 Plex Web UI 的 Discover/Browse 中搜索该资源
2. 打开详情页，从 URL 中提取 key（如 `/library/metadata/5d9c089bba6eb9001fba73de`）
3. 用 `plex://movie/<id>` 格式的 guid 直接调用 match API 匹配

### Plex 数据库中确实不存在的资源无法匹配
极少数冷门资源可能在 Plex 的元数据源（TMDB/TheTVDB）中不存在，无法自动匹配。这些应该报告给用户，由用户手动处理，**不要强行匹配到错误的结果**。

### Plex API 不支持 Unmatch
Plex 的 match API 只能将资源匹配到某个 guid，无法通过 API 取消匹配（Unmatch）。取消匹配只能在 Plex Web UI 中操作：点击 `...` → Match... → Unmatch。

### refresh 接口是异步的
`/library/sections/<key>/refresh` 返回 200 只代表任务已入队，不代表扫描已经完成。必须继续轮询 `/library/sections`，确认目标库的 `refreshing="0"` 后再开始批量匹配。

### 无头/NAS 环境下，`trakt-login` 可能直接失败
在某些 NAS / 精简 Python 环境里，`plextraktsync trakt-login` 可能因为 `RemoteDisconnected`、`requests-cache`、TLS 或上游短暂波动而直接失败。遇到这种情况时：
1. **不要** 一直重试同一条 `trakt-login`
2. 先确认 `https://api.trakt.tv/oauth/device/code` 直连可用
3. 改用本 skill 的 `scripts/plex_trakt_device_login.py` 走手动 device auth
4. 授权成功后，确认 `.pytrakt.json` 和 `.env` 已写好，再继续 `sync`

### Plex Online watchlist / liked lists 可能拖垮整个 sync
`plextraktsync sync` 不只访问本地 PMS；如果启用了 Plex watchlist / liked lists，同步过程中还会访问 Plex Online（如 `metadata.provider.plex.tv`）。这条链路在某些网络环境里会超时，导致整个 sync 提前退出。

如果核心目标只是完成 **watched / rating** 同步，而 Plex Online 访问不稳定，应临时关闭：
- `sync.plex_to_trakt.watchlist`
- `sync.trakt_to_plex.watchlist`
- `sync.trakt_to_plex.liked_lists`

然后先完成核心同步，并在最终报告里明确说明 watchlist / liked lists 本次被跳过。

### 某些 Trakt watched-show 响应会返回 `seasons: null`
实操中遇到过 `plextraktsync sync` 在 `plextraktsync/pytrakt_extensions.py` 崩溃，报错：

```text
TypeError: 'NoneType' object is not iterable
```

根因是 Trakt 的部分 watched-show / season 数据字段可能返回 `null`，而 PlexTraktSync 当前版本直接迭代了 `shows` / `seasons` / `episodes`。

如果命中这个问题：
1. 先确认报错栈是否落在 `pytrakt_extensions.py`
2. 对本地安装的 PlexTraktSync 做最小兼容修复：把 `shows`、`seasons`、`episodes` 的 `None` 当空列表处理
3. 修完后再重跑 `plextraktsync sync`

### 少量外语片罗马字标题仍可能进入人工复核
即使已经结合 `originalTitle` 和 `slug` 做相似度校验，仍可能有少量外语片因为文件名使用**罗马字/英译名**、而 Plex 元数据使用**中文名或原文字**而被标记为可疑，例如：
- `Jagten` ↔ `The Hunt`
- `Kimi to Nami ni Noretara` ↔ `若能与你共乘海浪之上`
- `Gamlet` ↔ `哈姆雷特`

这类项目要优先人工确认，**不要因为 verify 报警就强行改成别的匹配**。

### 实操复盘：这次为什么会“反凑”

这次真实出现的错配，根因主要有 4 类：

1. **之前的 verify 对 show 检查不完整**
   - 只看电影 `Part` 文件路径是不够的；动漫/电视剧很多条目只有 show 级 `Location` 路径
   - 这会漏掉明显错配，例如：
     - `大清风云` 实际目录是 `大宋提刑官2...`
     - `斗罗大陆Ⅱ绝世唐门` 实际目录是 `Jujutsu.Kaisen.S02...`
     - `特工科恩` 实际目录是 `SPY×FAMILY`
     - `舞法天女之绚彩归来` 实际目录是 `Rick and Morty S04`
     - `镖人` 实际目录是 `进击的巨人第一季第二季第三季`

2. **不能把“只重叠一个词”当成正确匹配**
   - `SPY×FAMILY` 和 `The Spy / 特工科恩` 只共享一个 `spy`
   - `Made in Abyss S02` 和 `玛露露库的日常` 共享了同 franchise，但不是同一作品
   - 结论：**单词级弱重叠**、**同 franchise 子集重叠**，都只能进人工复核，不能直接自动修

3. **短英文标题同名碰撞风险很高**
   - `Myth of Love` 这类短英文名，Plex 很可能命中另一个同名作品
   - 即使标题和年份都对，也不代表作品就对
   - 结论：如果文件名是**短英文别名**，而官方中文标题与它几乎没有 token 重叠，就必须人工确认，必要时让用户给出目标标题

4. **合集/季包目录很容易把 show 带偏**
   - 如果目录本身是“第一季第二季第三季合集”“S01-S08”等打包名，Plex 有时会把它归到错误条目
   - 结论：这类目录要在 verify 结果里优先人工审查；修完后一定按目录归属回查

### 下次执行时的硬规则

1. **先 scan，再 match，再 verify**
2. **verify 必须覆盖 show 的 `Location` 路径**，不能只看 movie `Part`
3. **高置信度错配才自动修**
   - 完全不同作品
   - 用户明确给出“错误标题 → 正确标题”
   - 修完后可按路径归属证明成功
4. **以下情况只进人工复核**
   - 只共享 1 个英文词
   - 同 franchise 但像 parent show / spinoff
   - 短英文别名 / unofficial English alias
   - 合集目录 / 多季打包目录
5. **修完后一定按原始路径回查**
   - 对 show，允许旧 `ratingKey` 消失
   - 只要原始目录已经并入正确作品，就算修正成功

---

## 诊断入口：直连 SQLite 做全库审计

REST API 逐条查一万多个条目慢到不可用。**直接只读打开 Plex 的 SQLite 库**，
一次查询就能把全库的匹配状态、路径归属、观看记录全部拉出来，是审计的唯一实用入口。

```python
import sqlite3, urllib.parse
con = sqlite3.connect("file:" + urllib.parse.quote(DB) + "?mode=ro", uri=True)
```

- **永远只读**（`mode=ro`）。PMS 在跑，写库会撞锁甚至损坏。
- QPKG 安装目录下的 `Resources/com.plexapp.plugins.library.db` 是 ~300KB 的**空壳**，
  真库 100MB+，在 `Plug-in Support/Databases/`。`scripts/_config.py` 已按体积自动排除。
- 路径含空格，必须 `urllib.parse.quote`。

### schema 里几个反直觉的点

```text
library_sections        没有 location_id；section_type 是整数（1 movie / 2 show / 3 artist / 4 photo）
section_locations       列名是 library_section_id + root_path + available + scanned_at
media_items             没有 root_file —— show 级路径 DB 里根本不存
tags / taggings         tag_type 在 tags 上，taggings 没有这一列（1=genre, 6=cast, 8=country）
metadata_item_settings  用 guid 关联，没有 metadata_item_id 列
media_parts             有 deleted_at；container 在 media_items 上，不在 media_parts
metadata_items.index    index 是 SQLite 保留字，必须写成 [index]
metadata_type           1=movie 2=show 3=season 4=episode 12=clip/extra
```

`metadata_type=12` 的 clip/extra 其 `library_section_id` 为 NULL。
失效媒体行如果全是 type 12，那是花絮残留，不影响任何库的条目数，可以忽略。

show 级路径只能反推：取该剧全部 episode 的 `media_parts.file`，求 `os.path.commonpath`，
再判断 basename 是否形如 `Season N` / `Sxx` 决定要不要再上跳一层。

### sqlite3 参数必须传元组

```python
c.execute(sql, sid)    # ProgrammingError: parameters are of unsupported type
c.execute(sql, (sid,)) # 对
c.execute(sql, (s,))   # 字符串也要加逗号，否则被当序列逐字符拆开
```

### 统计口径：数「文件」，不要数「集槽位」

扫描器放弃解析集号时，会把**整个目录的文件全塞进一个幻影槽位**
（例如标题里的数字被当成集号，或年份被当成集号）。这时按
`count(distinct episode)` 统计只会报「1 集未匹配」，而实际有几十个文件没被索引。

必须下钻到 `media_parts`：**一个槽位上叠了多个文件 = 扫描器没解析出编号**，
这是最严重的一类，要单独列出来。「未匹配集数」和「受影响文件数」是两个不同的数字，
报告时都要给。

### `unmatched` 的判据不能只看 `local:`

未匹配的 guid 形态有多种（`local://`、`agents.none://` 等）。只判 `local:%`
会得到「0 未匹配」的假阴性。先把全库 guid 前缀 `distinct` 出来看清楚再写判据。

---

## REST API 的硬边界

全部实测，踩过的坑：

| 动作 | 方法 | 备注 |
| --- | --- | --- |
| 扫描库文件 | **GET** `/library/sections/<id>/refresh` | PUT 会 **404** |
| 重建单项元数据 | **PUT** `/library/metadata/<k>/refresh` | GET 会 404 |
| 清空回收站 | **PUT** `/library/sections/<id>/emptyTrash` | GET/`/trash`/`/clean` 全 404 |
| 应用匹配 | **PUT** `/library/metadata/<k>/match` | 见下 |
| 改库配置 | **PUT** `/library/sections/<id>?...` | 见下 |

**方法搞错就是 404，body 是一行 HTML，没有任何提示。**

### match 必须带齐 4 个参数

```
PUT /library/metadata/<k>/match?guid=<percent-encoded>&name=<title>&year=<year>&language=zh-CN
```

只给 `guid` 会返回 **400**，body 同样是 HTML，不会告诉你缺了什么。
guid 里的 `://` 必须 percent-encode。

### `/matches` 返回的是 `SearchResult`，字段是平铺的

```python
d = get("/library/metadata/%s/matches?manual=1&type=2&language=zh-CN&title=%s&year=%s" % ...)
for s in d["MediaContainer"]["SearchResult"]:
    s["name"], s["year"], s["guid"]     # ← 平铺
```

**不是** `<Directory>`，也**没有** `Metadata` 子对象。按 `SearchResult[].Metadata.title`
解析会全部拿到 `None`，看起来像「搜到了 6 条但全是空的」。

### `GET /library/sections/<单个 id>` 不是配置

它返回的是该库的 "All xxx" hub。**读配置必须用 `/library/sections`（复数）**。

### 改库配置必须带齐所有参数

```
PUT /library/sections/<id>?type=show&name=<n>&agent=<a>&scanner=<s>&language=<lang>
    &metadataAgentProviderGroupId=<g>&location=<p1>&location=<p2>
```

- 少一个参数就会被**清空**，不是保留原值
- `location=` 要重复写，**不是** `location[]=`
- `metadataAgentProviderGroupId`：电影库 = 1，剧集库 = 2，容易漏
- 改前先把 `/library/sections` 存成 XML + JSON 两份备份

### API 做不到的两件事

1. **不能 unmatch**。`/library/metadata/<k>/unmatch` 在 GET/PUT/OPTIONS 下全部 404。
   只能在 Web UI 里 `…` → Match → Unmatch。
   —— 但**重新 match 到正确的 guid 是完全可行的**，会直接覆盖旧的，
   绝大多数情况根本不需要 unmatch。
2. **不能拆开已合并的条目**。两部片被并成一个条目后，API 无法拆分，
   只能靠改文件名/目录名让扫描器重新识别。

### 等待扫描结束的两个坑

1. `GET /library/sections` 的 JSON 里列表 key 是 **`Directory`**，不是 `Metadata`。
   写错会拿到空列表，轮询脚本第一轮就判定「全空闲」直接退出。
2. 真正代表「还在干活」的是 `/activities` 里的
   **`library.update.item.metadata`** 和 **`library.update.section`**。
   只盯 `sections` 的 `refreshing`/`scanning` 标志会误判——实测两个标志全程
   `False`/`None`，而 metadata job 跑了 24 分钟。
   同时要忽略常驻的 `media.generate.intros` / `media.generate.credits`，它们永远在跑。

用 `scripts/plex_wait_idle.py`，别用 `sleep` 猜。

---

## 头号问题类：文件名 → 季/集号解析失败

**绝大多数「未匹配」和「错配」的根因都在这里**，不在元数据源。

### 目录名全是 `[...]` 组 → 标题解析为空 → 多部作品被吞成一部

扫描器把目录名里所有 `[...]` / `(...)` 当发布组和规格标签剥掉。
如果剥完**什么都不剩**，这些目录就成了同一个「无名 show」，
**跨 library root 也能合并**（不同国家目录里的东西被并成一部）。

```text
[SubGroup][作品名][BDRIP][1080P]        -> 剥完 = 空
[另一个组][另一部作品名][1080p][JPSC]    -> 剥完 = 空
```

后果极其隐蔽：**season/episode 的 guid 全是正确的**，
所以「未匹配集数」统计完全看不出来。错的是每一集挂了 2~3 个**别的作品的视频文件**
当「多版本」。

**唯一的强信号：集数正常，但 `media_part` 数是集数的整数倍。**
审计脚本必须同时统计 `count(distinct episode)` 和 `count(media_part)`。

修法：**只改入侵者的目录名**，本体不动（本体已正确匹配，改名会重建条目、丢观看状态）。
新目录名必须含一个不在方括号里的裸标题。改完 rescan，被吞的作品会变成新条目，
通常是 unmatched，需要手动 match。

`scripts/absorb_audit.py` 可以生成嫌疑清单，但**误报率很高**
（目录用罗马字、Plex 标题用中文的情况全会命中），只能当线索，每条都要人工核。
真正的判据还是上面那条 part/集数比。

### 目录名以数字开头 → 数字劫持集号解析

```text
12.3 作品名 （全9集）2020/12.3 作品名.EP01.mp4
```

9 个文件可能全被塞进同一个幻影槽位（例如 `S1E12`）。
**光给文件加 `S01Exx` 不够，目录名也要改**，把开头的数字挪到后面。

### 文件名不是合法 UTF-8 → 集号读不出，退化去抓年份

非 UTF-8 字节（常见于 Shift-JIS 命名的日式资源）会让扫描器读不出
`第NN話` 这类本地化集号，退而在文件名里抓一个四位数当集号，
于是整个目录几十集全叠进 `S1E<年份>` 一个槽位。

检测：`os.listdir()` 返回的 str 能否 `.encode("utf-8")`；
或逐个 `iconv -f UTF-8 -t UTF-8` 校验。
**别用 shell 的 `while read` 测**——locale 会造成假阳性。

修法是重命名成合法 UTF-8 + 显式 `SxxEyy`。顺手把同名的 `.srt`/`.ass` 一起改，
否则字幕会失去关联。

### 裸 `Exx`（无季号）一律当 S1

第二季的文件如果只写 `E01`，会被并进 S1 当「多版本」，
表现为 S1 集数正常但 part 数翻倍，同时**整季缺失**。
补上 `S02` 前缀即可。

### `[NN]` 方括号集号需要显式转成 `SxxEnn`

BD 合集常见 `[01]`…`[25]` 命名。转换时要有一张**逐目录的显式季号表**，
不要靠推断——同一目录里可能混着正片、OVA、SP。

### 版本号写法会逃过集号正则

`\[(\d{1,3})\](?!\d)` 匹配不到 `[12v2]` 这类带版本后缀的写法。
**改名脚本跑完必须回头数一遍：目录里的视频文件数 vs Plex 索引到的 part 数**，
差多少就是漏了多少。

### 统计未索引文件时必须排除点号目录

`.@__thumb/` 里的缩略图**文件名后缀也是 `.mp4`**。
不排除的话数字会虚高一大截（实测 2900 vs 真实 1756）。
同时要排除 `@Recycle`、`@eaDir`。

### 季号错位：先确认 TMDB 的建模方式，两个方向都存在

有的剧 TMDB 按「一季」建模而本地按多季分目录，有的反过来。
**必须开 TMDB 季页面数集数**，绝不能凭直觉推断该加还是该减偏移。

改名之后的固定收尾：新条目要 **match 回父剧的 guid**，否则会变成独立的一部剧。

### 试错过但无效的手段

- **移出 library root → rescan → 移回 → rescan**：对电影有效（会生成新条目），
  对**卡住的 show 季映射无效**。
- 指望 rescan 自己修好编号问题：不会，扫描器只认文件名。

---

## 第二类问题：元数据源挂错（TVDB vs TMDB）

### 整季未匹配 ≠ 上游没收录，先查 `Guid[]`

Plex 的 TV agent 同时接 TMDB 和 TVDB，一部剧可能落到任一个源上，
而两个源的季/集收录完整度经常不一样。**整季全未匹配时，第一件事是查源**：

```python
d = get("/library/metadata/%s" % ratingKey)["Metadata"][0]
ids = [x["id"] for x in d.get("Guid", [])
       if x["id"].split("://")[0] in ("tmdb", "tvdb", "imdb")]
```

把所有受影响剧集的外部 ID 一次拉出来，**缺 `tmdb://` 的那个就是异常值**。

### 换源的方法

`/matches` 搜同一个标题时，**同名同年经常返回两个候选**（一个 TMDB 源、一个 TVDB 源）。
从 `plex://show/<24位hex>` 看不出哪个是哪个，**只能试**：match 过去之后再读一次
`Guid[]` 验证。试错可逆（记下旧 guid 就能回退），
但**动手前先查这部剧的观看记录数，改完再核对一次没丢**。

---

## 第三类问题：搜索没穷尽就下「上游不存在」的结论

这是最容易犯的错。`/matches` 的搜索质量高度依赖关键词，
**「搜不到」几乎从来不等于「不存在」**。固定升级顺序：

1. 搜**光秃秃的主标题**，看前 20 名（不要只看前 5）
2. 搜**主标题 + 中文后缀变体**：`（解说版）`、`解说版`、`特别版`、`完整版`、
   `剧场版`、`国语版`、`未删减`、`纪念版`
   —— 二创/解说/剪辑版在 TMDB 上**是有独立条目的**，而且常常就排第一，
   但只搜主标题时它会掉到 12 名开外
3. 搜**主标题 + 年份**、主标题 + 平台名
4. 搜**外文原题 / 罗马字 / 英译名**，中文内容优先用中文名
   （英文名可能命中同名的别国作品）
5. 让用户在 Plex Web UI 的 Discover/Browse 里搜，从 URL 提取 key，
   用 `plex://movie/<id>` 直接 match
6. **最后**才允许说「确实不存在」

用户给出 TMDB/TVDB 链接时，**视为最高优先级事实**，直接按它 rematch，
不要再依赖相似度猜测。

### 判定「上游缺口」的唯一方法：开 TMDB 季页面数集数

不能凭「本地比 Plex 多几集」推断。逐个开季页面确认，结果分三种：

| 情况 | 处理 |
| --- | --- |
| TMDB 该季集数 < 本地 | 真缺口，**不要强修**，报告给用户 |
| TMDB 有 Season 0 且收录了该特辑 | 改文件名成 `S00Exx` 即可 |
| TMDB 该季**自己跳号**（有 E1、E3，缺 E2） | TMDB 的数据洞，无解 |

⚠️ **别假设特辑一定进 Season 0**。有的剧压根没有 S0（`/season/0` 返回 404），
它的圣诞特辑被编进了某一正常季的末尾。每部都得单独开页面确认。

### 双集连播拆分：不要改编号

发行版常把两集连播拆成两个文件（文件名里直接写着 `S09E23E24`），
而 TMDB 记为一集。**这类不能改编号**——改了会把两集内容错挂到一集上，
比重号更难查。识别特征：文件名自带双集记号，或 TMDB 集数比本地少且少的都是季末。

### 花絮不要塞进 Season 0

幕后特辑、制作纪录片这类，如果 TMDB 的 S0 里确实有对应条目就改 `S00Exx`；
如果没有，把所在**目录名改成点号开头**（`.Extras`），Plex 会自动从剧集列表里摘掉。
判断依据：文件名里自带 `Extras` / `Making of` / `幕后` 字样。

---

## 移动和改名的连带损伤

### 重命名会让 Plex 丢掉已有的匹配

对 show 尤其明显：改了目录名，Plex 会**删掉旧条目、建一个新的 unmatched 条目**。
所以任何批量改名前，先存身份快照。

### 电影库内部搬目录，匹配一定会掉一部分

**电影库不按媒体签名可靠重连。** 在同一个 library root 内部把文件搬到子目录，
Plex 的行为是**新建一个 `local://` 条目**，旧条目还留着，于是同一部片出现两份
（一份匹配好、一份未匹配），条目总数上升。

掉的比例不高但**完全无法预测哪几个会掉**。所以搬之前必须先做快照：

```bash
python3 scripts/movie_identity.py  /tmp/pre.json     # 搬之前
# ... 移动 ...
curl "$PLEX_BASE/library/sections/<id>/refresh?X-Plex-Token=$TOKEN"   # GET
python3 scripts/plex_wait_idle.py 1800
python3 scripts/movie_move_check.py /tmp/pre.json          # 先看
python3 scripts/movie_move_check.py /tmp/pre.json --fix    # 再补
```

关键是**用文件名 basename 做键**——移动只改目录不改文件名，basename 稳定。
补匹配时直接用快照里的 guid，**不走 `/matches` 搜索，不存在挑错条目的风险**。
两个条目 match 到同一个 `plex://movie/<id>` 后 Plex 会自动合并成「一部多版本」。

剧集侧同理有 `scripts/show_identity.py`（存 show + episode 两级 guid）。
实测剧集改目录名时 Plex **有时**仍能按签名重连，但不可依赖，一样要先快照。

### 电影库不要为子目录加 location；剧集库必须加

| 库类型 | root 的直接子目录被当成 | 需要为子目录注册 location 吗 |
| --- | --- | --- |
| 剧集库（show） | **一部剧** | **要** |
| 电影库（movie） | 普通目录，**递归往下找片子** | **不要** |

给电影库加嵌套 root（如 `Movies/` 和 `Movies/Collections/` 同时注册）是**有害的**：
同一批文件会被两个 root 各扫一遍，直接产出成百上千个重复条目。

验证方法：搬完之后统计各顶层目录的媒体分片数，总数不变就说明单 root 递归够用。

### 换 library root 会触发全量重新发现

新增/更换 root 时，Plex 会重新扫描整个库：既会发现之前未索引的目录，
**也会丢掉一批已有匹配**。必须准备好重新 match，且**重新发现时的自动匹配会挑错条目**，
每一条都要复核（尤其是短英文标题、同 franchise 的 spinoff、合集目录）。

### 一张碟装多部影片时，按年/按片拆分，不要整体移动

一个目录里放了两部不同影片（常见于 BD 原盘或双片碟）时，
整体移动到任何单一分类都会有一部落错。做法：先把次要那部的文件抽出去建独立目录，
再移动剩下的碟目录，`.nfo`/`.Extras`/`Making.of.*` 跟着主片走。

电影库还有个陷阱：**一个目录 = 一部电影，目录里多余的视频文件全被当成「多版本」**。
花絮、预告、第二部影片要移进点号目录（`.Extras`）或拆出去。

### 移动之后必须重跑 unmatched 统计

不能假设「只是换个目录」。每次批量移动/改名后都要重新核对条目总数和未匹配数。

---

## library root 与回收站：失效条目为什么删不掉

### `available=NULL` 的空 root 会触发「保护性不删除」

某个 library root 在磁盘上是**空目录**时，Plex 会把 `section_locations.available`
留成 NULL（它只在扫到实际媒体时才置 1），把它当成「可能没挂上盘」。日志里明确写：

```text
* Scanning <root>
* Marking media not available in <root>
* Taking N items out of the map (which has N items) for being unavailable.
```

**「taking out of the map」是保护，不是删除**——Plex 把这些条目从「待删列表」里摘出去了。
所以 `autoEmptyTrash=True` + `allowMediaDeletion=True` 都不生效，`deleted_at` 永远是 NULL。

`PUT /library/sections/<id>?...&location=<同样的路径>` **不会**重置这个 flag
（Plex 按 path 复用旧行）。`emptyTrash` 也是空转，日志显示
`About to destroy 0 deleted items`。

**唯一有效的做法：先摘掉这个 location，等 ~20s，再原样加回去。**
摘掉时该 root 下的条目会被清除，加回去后配置与原来完全一致。
`available` 加回去后仍是 NULL，**无害**，它只影响删除保护。

⚠️ 这是删条目的操作。动手前先确认该 root 下**确实没有真实文件**，
且要保留的条目都在别的 root 下。

### 失效媒体行会让「数字对不上」

`media_parts.file` 指向已不存在的文件时，条目数看着正常但播放不了。
按 root 前缀分组统计 dead parts，能立刻看出是哪次搬迁/删除留下的。

判断一条 dead path 是不是真失效时注意：QNAP 上可能存在
**同一个文件系统被挂到两个路径**的情况（`stat` 出来的 `dev`/`inode` 相同），
这时其中一个路径下的内容其实根本不存在。

---

## 验证与收尾

### 每次大改动前后各跑一遍，diff 就是本轮净效果

```bash
S=<skill-dir>/scripts
python3 $S/plex_snapshot.py     /tmp/snap_before.json
# ... 改动 ...
curl "$PLEX_BASE/library/sections/<id>/refresh?X-Plex-Token=$TOKEN"   # GET
python3 $S/plex_wait_idle.py 3600
python3 $S/plex_final_verify.py /tmp/verify_after.json
python3 $S/absorb_audit.py
python3 $S/plex_snapshot.py     /tmp/snap_after.json
```

| 脚本 | 作用 |
| --- | --- |
| `plex_snapshot.py` | 各库条目计数、未匹配数、集级匹配率、**观看记录行数**、dead parts、缺 title_sort 的条目 |
| `plex_final_verify.py` | 每个条目是否在正确的库 / 国家目录 / 年份桶；输出六类清单 |
| `absorb_audit.py` | 找「被吞并」的 show（一部剧的 parts 来自多个语义无关目录） |
| `unmatched_files.py` | 按**文件**而非集槽位统计未匹配，并区分「幻影槽位」和单集缺失 |
| `plex_wait_idle.py` | 阻塞到扫描 + 元数据刷新全部结束 |
| `movie_identity.py` / `show_identity.py` | 改动前存 basename → guid 身份快照 |
| `movie_move_check.py` | 改动后 diff 快照，`--fix` 用记录的 guid 精确补回 |

**`plex_snapshot.py` 的观看记录三项（rows / viewed / rated）必须前后完全一致**，
这是唯一能证明「整理没有破坏用户数据」的指标。

### `title_sort` 独立于匹配

- rematch **不会**更新 `title_sort`
- 本地化脚本（CLP）见到已有值就跳过，**不会补救**

标题变了之后要手动清一次再重跑：

```bash
curl -X PUT "$PLEX_BASE/library/metadata/<k>?titleSort=&X-Plex-Token=$TOKEN"
```

### 修完一定按原始路径回查

对 show 来说，「路径归属正确」比「旧 ratingKey 还在」更能证明修正成功——
match 到已存在的 show 后旧条目会被并入，旧 ratingKey 消失是正常的。

### 收尾清单

1. 全库 rescan 并等到真正空闲（不是看 `refreshing` 标志）
2. 条目总数、未匹配数（show / movie / **episode 三级**）与改动前对比
3. 观看记录三项与改动前**完全一致**
4. dead media parts 数量与归属
5. 本地化脚本重跑一遍
6. 所有改名/移动的**回退映射 JSON** 落盘到 `PLEX_BACKUP_DIR`
7. 提醒用户刷新浏览器加载新元数据

---

## 已确认**不是**错误的模式（别报为问题）

- **多版本**：同一集/同一部片有多个 part，来自不同 encode。正常。
  判断标准是 part 对应的**目录语义是否同一部作品**，不是 part 数量。
- **电视剧季别年份差异**：Plex 按 show 级匹配，`year` 是整部剧首播年，
  不是具体季的年份。
- **外语片 `originalTitle` 与文件名不同**：韩语/日语/法语片匹配后中文标题
  与文件里的外语原名不同，是正常行为。
- **繁简体差异**：不是错配。
- **合集文件夹导致的路径/标题不匹配**：多个电影文件放在同一合集目录里，
  目录名和单片标题自然不同，验证脚本要跳过。
- **罗马字/英译名标题**：文件名用罗马字而元数据用中文名或原文字
  （如 `Jagten` ↔ `The Hunt`），要人工确认，**不要因为报警就强改成别的匹配**。
- **目录用罗马字、Plex 标题用中文**：`absorb_audit.py` 的主要误报来源。
- **`available=NULL` 的 root**：只影响删除保护，不影响扫描和索引。

### 弱重叠不能当正确匹配

- 只共享 1 个英文词（`SPY×FAMILY` vs `The Spy`）→ 只能进人工复核
- 同 franchise 的子集/衍生（parent show vs spinoff）→ 只能进人工复核
- 短英文别名与官方中文标题几乎没有 token 重叠 → 必须人工确认
- 合集/多季打包目录（`第一季第二季第三季`、`S01-S08`）→ 优先人工审查

**单词级弱重叠和同 franchise 子集重叠都不能自动修。**

---

## 环境依赖（QNAP 特有）

skill 脚本需要 `requests` 等第三方包，而 QNAP 上的 python3 往往来自 Entware：

```bash
export PYTHONPATH="$PHASE78_DIR"
python3 -c "import requests; print(requests.__version__)"
```

Entware 二进制把 ELF 解释器硬编码为 `/opt/lib/ld-linux-x86-64.so.2`，
`grep`/`find`/`xargs` 还依赖 `/opt/libexec/*-gnu`。
**如果 `/opt` 被 QPKG 安装器改成了真实目录，所有脚本报
`No such file or directory`（文件其实在）**。跑之前先确认：

```bash
ls -l /opt/lib /opt/libexec
```

后台跑脚本时 `PYTHONPATH` 要在**同一条命令里** export，否则会
`ModuleNotFoundError` 静默失败。

git 推送需要注意：Entware 的 `git` 默认**不带 HTTPS 传输**，
`git clone https://...` 会报 `remote-https is not a git command`，
需要 `opkg install git-http`。
