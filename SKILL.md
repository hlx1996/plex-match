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

## 2026-10-27 全库文件级彻查的新增结论

这一节来自一次「逐文件核对」的实操：13304 个本地视频文件、694 电影 / 370 剧 / 716 季 / 11154 集，全部下钻到 `media_parts` 级别核对。下面每条都实测验证过。

### 脚本级缺陷（必须修，否则会漏判）

1. **`unmatched` 判据漏掉 `agents.none`，导致「0 未匹配」是假的**
   `plex_match.py` 与 `plex_verify.py` 都报告 `unmatched: 0`，但库里实际有 1 项未匹配：
   ```
   key=23186 '华灯初上' guid=tv.plex.agents.none://23186 year=None
   ```
   未匹配的 guid 前缀不止 `local://`，还有 `tv.plex.agents.none://` / `com.plexapp.agents.none://`。
   **正确判据**：`guid LIKE '%agents.none%' OR guid LIKE 'local://%'`。
   最可靠的做法是直接查库统计，而不是靠 API 返回的字段猜。

2. **只按「来源目录」分组会完全看不见嵌套目录**
   真实事故：`华灯初上...EP01-24/` 目录里嵌了一整个 `后宫甄嬛传.Empresses.in.the.Palace.2011.../` 子目录（76 集）。Plex 把它们扫成了同一部剧的 Season 2，而整个条目又是 `agents.none` 未匹配 → **甄嬛传 76 集在库里长期没有任何元数据**。
   按目录分组时这 100 个文件只算「1 个来源目录」，检测器完全静默。
   **必须按文件 basename 聚类，并额外检测 show 目录下的嵌套发布目录**（判据：子目录名含年份/分辨率/发布组特征且含 ≥3 个文件）。

3. **身份判定必须用 Plex 自己的 `/matches`，不能用字符串相似度**
   同系列兄弟作品靠字符串永远分不开：`唐朝诡事录` / `唐朝诡事录之西行` / `唐朝诡事录之长安` / `唐诡奇谭` 在 TMDB 是 **4 个独立 guid**。
   正确流程：对每个文件簇，拿簇标题去 `/library/metadata/<key>/matches?title=...` 搜索，**比较返回 guid 与条目自身 guid**，不一致才是真混装。
   实测：字符串相似度法产生 523 条 mismatch 误报（几乎全是罗马字文件名 vs 中文标题），而 guid 比对法把 431 个候选收敛到 **1 个真问题**。

### 文件名解析器的坑（全部实测踩过）

| 坑 | 错误表现 | 修法 |
|---|---|---|
| `S12E01` 连写 | 季号正则的后顾断言 `(?![a-z0-9])` 让 `S12` 匹配失败 | 季号后只排除数字：`(?![0-9])`；集号前只排除字母：`(?<![a-z])` |
| 开头方括号是发布组 | `[Yousei-raws] Kimi ni Todoke 01` 被当成片名 `Yousei-raws` | 方括号仅在**含 CJK** 时当片名；拉丁片名优先取正文，方括号只兜底 |
| 纯分集命名 | `S12E01 Three Act Tragedy.mkv` 把**分集标题**当作品名，搜出完全无关的剧（大侦探波洛 70 个文件产生 70 个假簇） | 解析后作品名为空则跳过该文件，不下结论 |
| 多季打包目录 | `Friends.S01-S10/` 里的单集文件名无 Sxx 时，回退去抓目录名的 `S01`，导致第 2~10 季全部「季号不符」（216 条假阳性） | 季号只认**文件 basename**，绝不回退到目录名 |
| 片名里带年份 | `寒战1994.Cold.War.1994.2026.2160p` 有两个年份，扫描器取第一个 → 匹配到 1994 年的《赤子冰心》 | 取**最后一个**紧跟分辨率之前的年份；标题内的年份不参与候选过滤 |
| slug 拆词做别名 | slug `tang-zhao-gui-shi-lu-zhi-xi-xing-3` 拆出的 `tang` 命中 `strangetalesoftangdynasty`，整个系列都被判为「匹配」 | **不要把 slug 按分隔符拆成短 token 加入别名集** |

### API 能力边界（实测）

- `PUT /library/metadata/<key>/match` ✅ 可用，movie/show 都行
- `PUT /library/metadata/<key>/unmatch` ❌ **404**，API 确实不支持取消匹配
- `GET /library/sections/<k>/refresh` ✅（注意是 **GET**，用 PUT 会 404）
- `GET /library/sections/<k>/clean` ❌ 404，此版本无 Empty Trash 端点
- `GET /library/metadata/<key>/refresh` ❌ 404，条目级元数据刷新无此端点

### 合并条目无法用 API 拆分（重要）

实测尝试：把 S03/S04 目录移出库 → 重扫（条目降到 40 文件，季 3/4 被自动清除）→ 移回 → 重扫。
**结果：61 个文件被重新挂回原条目**，净变化为零。因为扫描器会按目录名里的系列关键词重新归并到已有 show。

所以拆分只能这样做：
1. Web UI 里对该条目 `...` → Match → **Unmatch**
2. 把要拆出去的目录移出库根目录
3. 重扫，先把留下的那部匹配好
4. **一次只移回一个目录**，重扫 → 立刻匹配 → 再移下一个
   （一次全移回会被扫描器重新合并）

附带发现：**季号与 TMDB 不一致会导致集级元数据永远填不上**。目录标 `S02/S03/S04`，但 TMDB 里这三部都是独立剧集、各自只有 Season 1，Plex 无法把 `S01E01-40` 映射到本地 `S02E01-40`，结果 101 集全部 `local://` guid、无标题无简介。修法是把目录名里的季号改成 S01 或直接去掉 Sxx。

### 直接读 Plex 数据库做全库审计（强烈推荐）

比几万次 API 调用快几个数量级，且只读安全：

```python
import sqlite3, urllib.parse
con = sqlite3.connect("file:" + urllib.parse.quote(DB) + "?mode=ro", uri=True)
```

- 路径含空格必须 `urllib.parse.quote`；`mode=ro` 保证不会写入正在被 Plex 写的库
- 库是 **WAL 模式**（有 `-wal`/`-shm`），只读连接能看到已提交数据
- **但 WAL 快照在连续查询间可能不一致**，同一条目两次查询结果会不同。要拿权威的当前状态请用 API 的 `leafCount` / `childCount`
- `python3` 的 sqlite3 **不支持 `uri=True`**，必须用 Entware 的 python3.13
- 关键表：`library_sections`、`section_locations`(库根目录，`available=NULL` 表示该根目录当前不可用)、`metadata_items`(`metadata_type`: 1=movie 2=show 3=season 4=episode 12=在线预告片)、`media_items`、`media_parts`(`file` 为全路径)、`directories`
- **观看状态在 `metadata_item_settings`，按 `guid` 索引，不在 `metadata_items`**（那里没有 `last_viewed_at` 列）。判断操作是否丢了观看记录，就比对这张表的行数与 `last_viewed_at is not null` 计数

### 不是错误的情况（别再报为问题）

- **`metadata_type=12` 是在线预告片**（`extra_data` 里是 `at:key /services/iva/assets/...`），`file` 为空是正常的，审计要排除
- **合集目录**：一部电影同时存在于 `豆瓣高分100部`、`IMDb Top250`、`诺兰作品合集`、`哈利波特合集` 等多个合集里，会产生「一个目录喂给 N 个条目」和「一部电影有 N 个目录」，全部正常
- **同剧多源**：中英文目录名各一份（`Friends.S01-S10` + `老友记S01-S10.Friends`）、不同发布组各一份，属于用户刻意保留的多版本
- **TMDB 公映年 ≠ 影展年**：`A.New.Old.Play.2021`（椒麻堂会）在 TMDB 是 `year=2025 / originallyAvailableAt=2025-06-11`，文件名里的 2021 是影展年份，**不是错配**
- **电影目录里的附赠短片/花絮**（`Making.of.*.mkv`、`Ode.to.Joy.2008.mkv`）会被当成独立条目，属正常
- **Plex 标题含屏蔽词替换**：`去他妈的世界` 在 Plex 里显示为 `去他*的世界`，字符串比对会判 DIFF，实际同一部剧

### Trakt：应用失效与 token 过期是两回事

本次 `.pytrakt.json` 的 `OAUTH_EXPIRES_AT` 已过期 106 天，但问题不止于此：

- refresh 返回 `invalid_grant - session not found`
- 用存储的 `CLIENT_ID` 请求 `POST /oauth/device/code` 返回 **401**

两者同时出现 = **那个 Trakt API 应用本身已被删除或失效**，光刷新 token 没用，必须去 `https://trakt.tv/oauth/applications/new` 重新注册应用拿新的 client_id/secret。

**另一个要命的行为**：refresh 失败时 `plextraktsync sync` **不会中止**，而是对每一个条目重试刷新（实测 81 次刷新尝试、80 条 `Skipping`）。发现 `invalid_grant` 要**立刻停掉进程**，否则空转全库并可能触发 Trakt 限流。

`healthcheck` 通过 **不代表** token 可用 —— 本次 healthcheck passed，但 sync 立刻开始报 refresh 失败。判断 Trakt 是否真能用，要看 sync 的头几条日志。

### 环境依赖（QNAP 特有）

skill 的脚本需要 `requests`，而 QNAP 上唯一的 python3 是 Entware 的：

```bash
export PYTHONPATH="$PHASE78_DIR"
python3   # 例：某台 QNAP 上是 /share/CACHEDEV1_DATA/.qpkg/Entware/bin/python3 (3.13)
```

Entware 二进制把 ELF 解释器硬编码为 `/opt/lib/ld-linux-x86-64.so.2`，`grep/find/xargs` 还依赖 `/opt/libexec/*-gnu`。**如果 `/opt` 被 QPKG 安装器改成了真实目录，所有脚本报 `No such file or directory`（文件其实在）**。跑 skill 前先确认：

```bash
ls -l /opt/lib /opt/libexec && python3 -c "import requests; print(requests.__version__)"
```

后台跑脚本时记得 `PYTHONPATH` 要在同一个命令里 export，否则会以 `ModuleNotFoundError: requests` 静默失败。

## 2026-10-27 第二轮：集级（episode）未匹配才是真正的大头

上一轮的结论仍然不够。**show 匹配成功 ≠ 每一集匹配成功**，而本 skill 的 `plex_match.py` / `plex_verify.py` 以及"全库 0 unmatched"这类检查，**全部只看 show / movie 级**。实测这个盲区非常大：

- show/movie 级：**0 未匹配**（373 部剧 + 694 部电影，全部有 `plex://` guid）
- 同一时刻集级：**355 集未匹配，横跨 46 部剧**

这些集在 UI 里显示成 `Episode 1`、`Episode 2` 占位标题，用户看到的就是"海报有了但每集没名字"。**任何一次 match 收尾都必须补一次集级审计。**

### 集级未匹配的检测方法

`local://` 的 episode 行**不是垃圾数据，它们真实持有媒体文件**（`media_items` → `media_parts.file`），所以不是"可以忽略的残留"。只读查库：

```sql
-- 每部剧有多少集是 local://
select sh.id, sh.title, count(*) total,
       sum(case when e.guid like 'local:%' then 1 else 0 end) loc
from metadata_items e
join metadata_items s  on e.parent_id = s.id
join metadata_items sh on s.parent_id = sh.id
where e.metadata_type = 4
group by sh.id having loc > 0 order by loc desc;
```

判断严重程度的关键是**季 guid**：

| 季 guid | 含义 | 处理 |
| --- | --- | --- |
| `plex://season/...` 但该季集全是 `local://` | TMDB 认识这一季，只是集没抓到 | 先 `PUT refresh`，多数能自愈 |
| `local://NNNN` | Plex 在 TMDB 里**找不到这一季** | 要么季号错位（改文件名），要么 TMDB 真的没有 |

### API 层面的四个硬事实（都实测过）

1. **`PUT /library/metadata/<key>/match` 必须同时带 `guid` + `name` + `year` + `language`**。只给 `guid` 会返回 `400 Bad Request`（HTML 页面，没有任何提示）。guid 还要 percent-encode：

   ```bash
   curl -X PUT "$BASE/library/metadata/$K/match?guid=$(enc 'plex://show/xxx')&name=$(enc '剧名')&year=2024&language=zh-CN&X-Plex-Token=$TOK"
   ```

2. **`PUT /library/metadata/<key>/refresh` 是 200，`GET` 是 404**。（此前记录的"metadata refresh 不可用"是错的，当时只试了 GET。）本次单靠这一个调用就把 `莲花楼 40→0`、`画江湖之不良人 12→0`、`大侦探波洛 4→0` 修好了，**是性价比最高的第一步，务必先试**。

3. **`/library/metadata/<key>/matches` 返回的是 `<SearchResult>` 节点，不是 `<Directory>`**。按 `Directory` 解析会静默得到"无候选"，看起来像 Plex 搜不到，其实只是解析错了。搜索还必须带 `type=2&language=zh-CN&title=`，否则返回空。

4. **`/unmatch` 仍然是 404**，API 无法取消匹配。所以"匹配到了错误的剧"只能靠 `match` 改指到正确 guid，改不了就得让用户去 Web UI。

### 重命名会让 Plex 丢掉已有的匹配

这是本轮踩到的最意外的一点：**把一个已匹配 show 的目录/文件重命名后，Plex 会删掉原来的 show 条目，并按新名字建一个全新的 unmatched 条目**，不会保留原匹配。

实测：`地球脉动 3`(16902) 和 `蓝色星球 2`(10511) 改完名后 id 直接消失，变成 `local://32236` / `local://32246` 两个新条目。

所以流程必须是：**重命名 → rescan → 重新 match**。改完名看到"多出来几个 unmatched"不要慌，那是预期行为，match 一次就并回去了。

顺带一个有用的技巧：把新建的 unmatched 条目 `match` 到原剧的 guid，Plex 会**干净地合并**（旧 ratingKey 消失，季并入目标剧）。`咒术回战` 的 S2 用这招从 `local://32320` 并回了 19458。

### 移出/移回 **不能**修复卡住的集映射

试过把 `Jujutsu.Kaisen.S02` 目录移出媒体库、rescan、再移回、rescan：**Plex 不会把它挂回原来的 show**，而是新建一个 unmatched show。再 match 回原 guid 虽然能合并，但那些集**依旧是 `local://`**。

结论：`咒术回战 S2`(23)、`纸房子 S4/S5`(18)、`斯巴达克斯 S4`(10) 这种"show 已匹配、季在 TMDB 里确实存在、但集死活不映射"的情况，**refresh + 重新 match + 移出移回 + 合并 全部无效**，只能让用户在 Web UI 里 Unmatch → Match 强制重建 bundle。别再重复试这条链路。

### 季号错位：先确认 TMDB 的建模方式，两个方向都存在

这是本轮最容易搞反的地方。**同样是"文件写 S02"，正确做法可能完全相反**：

- **A. TMDB 把它建成独立剧、只有一季** → 要把 `S02/S03` 改成 `S01`
  实例：`地球脉动3`(Planet Earth III)、`蓝色星球2`(Blue Planet II)、`唐朝诡事录之西行/之长安/唐诡奇谭`
- **B. TMDB 把它建成父剧的第 2 季** → **绝对不能改成 S01**，要保留 `S02`，并把 show 匹配到父剧
  实例：`秘密森林2` = `秘密森林`(2017) 的 Season 2

**怎么区分**：搜 `剧名2` / `剧名 第二季`，如果 Plex 的 `/matches` **搜不到独立条目**，只能搜到父剧，那就是 B。

### "映射到第 N 集就停住"= 匹配到了另一部剧

这是识别 B 类错配最有用的信号。`秘密森林S02` 改名成 S01 后，16 集里 **E01–E08 映射成功、E09–E16 全是 local**。不是网络问题也不是刷新不够——是**匹配到的那部剧一共只有 8 集**。

查 `summary` 立刻现形：它匹配的是 Netflix 英剧 **The Stranger（2020，哈兰·科本小说改编，8 集）**，不是韩剧秘密森林2（16 集）。改回 `S02` 并 match 到 `秘密森林`(2017) 的 guid 后，S1(16) + S2(16) 全部 `plex://episode`。

**硬规则：只要出现"前 N 集映射成功、后面全部 local"，就去核对匹配到的那部剧总集数是不是正好 N。是的话就是错配，不是季号问题。**

顺带：改回 S02 时我把文件名前缀也换成了 `秘密森林.Stranger.S02E01`，因为 Plex 之后又自动匹配到了 `死亡森林的秘密`（2020）——**文件名前缀里带 "Stranger" + 2020 就会不断被吸到英文同名词条上**，前缀要以中文剧名开头。

### 其他已验证的可修成因

| 成因 | 实例 | 修法 | 效果 |
| --- | --- | --- | --- |
| show 名和文件名根本是两部剧 | `天降福宝之吉星高照` 挂在 `死刑将至.Si.Xing.Jiang.Zhi.2026` 的 27 个文件上 | 一次 `match` 指到正确 guid | 27 集全部拿到真实中文标题 |
| 发行组后缀被当成季号 | Clannad 的 `- mawen1250` → 造出**第 1250 季**，44 个 mkv 被二次索引成 22 集幻影 | 目录名和 `.mkv` 文件名都去掉 ` - mawen1250` | 22 集幻影消失，S1/S2 正确映射保留 |
| 只有 `Exx` 没有季号，而 TMDB 分两季 | `逆局` `E01..E24` 全塞进 S1，TMDB 是 12+12 | `E01-E12`→`S01E01-E12`，`E13-E24`→`S02E01-E12` | 12 集修复 |
| 整季元数据从未抓取 | 莲花楼 / 画江湖之不良人 / 大侦探波洛 | `PUT metadata/<key>/refresh` | 56 集自愈 |

### 确认是上游 TMDB 缺口、**不要强修**的情况

本轮 208 集残留里的大头都属于这类，硬凑只会把库改坏：

- `大宋提刑官2`：TMDB 该条目 S1 **只有 1 集**，本地 41 集。搜遍了也没有更完整的条目（`/matches` 第一名就是当前这个）。
- `三体`（Bilibili 版）：本地是 **51 集的完整剧情剪辑**（`第十六集面壁者雷迪亚兹` … `第五十一集三体大结局`），TMDB 的 2022 动画只有 15 集。这不是官方发行版本，TMDB 没有对应条目。
- 加长版/特辑编号超出 TMDB：`The Office` Extended 的 `S05E27/E28`、`Friends.S08E24`。
- 圣诞特辑被编成正片集号：`Downton.Abbey.S06E09.Christmas`（TMDB 放在 Season 0）、`Inside.No.9.S05E00`、`Sherlock.S01E00`、`Modern.Family.S11E00`、`Endeavour.S01E00`。
- 一个文件多集：`Lost.S06E17E18`（TMDB 是 E17、E18 两集，Plex 无法映射 `E17E18`）。
- `SP01`/`SP02` 特典：`短剧开始啦` S0 的 6 个 `Konto.ga.Hajimaru.SP0x`。
- 花絮被编成正常集号：`Mysterious.Lotus.Casebook.Behind.The.Scenes.S01E01` 和正片 `S01E01` **抢同一个编号**；`兄弟连(花絮)` 目录里的 `Band.of.Brothers.E01/E02`。这类应该移到 `Extras/` 或改名，而不是去 match。

### 审计脚本自身的坑（本轮实际踩到）

- **`index` 是 SQLite 保留字**，写 `e."index"` 在三引号字符串结尾会和 `"""` 撞成语法错误，用 `e.[index]`。
- `container` 在 `media_items` 上，不在 `media_parts` 上。
- `section_locations` 的外键列叫 `library_section_id`，不是 `section_id`。
- **审计脚本不要把输出路径写死**。我把 `OUT` 固定成 `/tmp/ep_audit.json`，第二次运行直接把基线覆盖没了，只能回头去解析日志文本做对比。输出路径要走 argv。
- **Plex 处理刷新期间会对合法 key 返回瞬时 404**（`/library/metadata/<k>/children`，重试 5 次仍 404，但 curl 同一 URL 是 200）。要么重试要么直接读库，不要据此判断条目"已消失"。
- 刷新过程中抓的快照不可信：本次同一份数据先后抓到 302 / 321 两个值。要等 `/library/sections` 的 `refreshing` 和 `scanning` 全为 0，再等 30–60 秒，才做统计。
- **重命名前必须先把回退映射落盘**（`kind`/`job`/`old`/`new`）。本轮靠它把 `陌生人` 那一组 18 项精确回退；回退时要**先在当前目录内改文件名、最后改目录名**，否则预检查会因为路径基准不对而全部报 MISSING。

### 收尾清单（每次 match 都要走完）

1. `PUT metadata/<key>/refresh` 全部受影响 show —— 最便宜，先做
2. 集级审计（上面的 SQL），按季 guid 分类
3. 只有确认是 A 类（TMDB 独立剧、单季）才改季号；B 类保留 `Sxx` 改 match
4. 重命名后**必须重新 match**，别指望旧匹配还在
5. show/movie 级 + 集级都要复查到稳定值
6. 核对 `metadata_item_settings` 行数没变（观看记录以 guid 为键，show guid 不变就不会丢）
7. 跑 CLP，给新条目补拼音排序标题

## 2026-10-27 第三轮：电影库要单独做文件级审计

集级审计的思路不能直接套到电影上——**电影的失效模式完全不同**。694 部电影 show 级 guid 全部正常、0 未匹配，但逐文件看下来有 4 类真实问题。

### 电影库的头号陷阱：一个目录 = 一部电影，多余的视频文件全变成"版本"

Plex 的电影扫描器把**一个目录当成一部电影**，目录里每个视频文件都成为这部电影的一个 alternate version。后果：

- `Nymphomaniac.Volume.1+2.2013...Grym/` 里同时放了 Vol.1 和 Vol.2 → `女性瘾者：第一部` 有 7 个"版本"，**其中 version 2 其实是第二部**。用户点播放选到它，就是拿第一部的元数据放第二部。
- `[2022 Movie][Karakai Jouzu no Takagi-san The Movie][BDRIP][1080P+SP]/` → `擅长捉弄的高木同学 剧场版` 有 **15 个版本，只有 1 个是正片**，其余是 `[Menu]`/`[Logo]`/`[Producer Logo]`/`[SP01]`~`[SP10]`/`[Tokuten BD]`。播放等于抽奖。
- `小黄人大眼萌` 4 个版本里 3 个是 `SP.*.mkv` 短片；`欢乐好声音` 同样；`椒麻堂会` 混进了 `Making.of.2021`。

**检测**：统计每部电影的 `media_items` 数量，>1 的都要看文件名。

```sql
select m.id, m.title, count(distinct mi.id) n
from metadata_items m join media_items mi on mi.metadata_item_id = m.id
where m.metadata_type = 1 group by m.id having n > 1 order by n desc;
```

花絮特征 token：`[Menu]` `[Logo]` `[Producer Logo]` `[SP\d+]` `[Tokuten BD]` `Trailer` `Teaser` `Preview` `Interview` `Making.of` `Q.and.A` `Bloopers` `Deleted` `Featurette` `Commentary` `^SP\.`。

**修法**：把花絮移进**点号开头的子目录**（`.Extras`）。Plex 跳过所有点号目录——这也是 `.@__thumb` 从来不会被索引的原因。实测 5 部污染 → 0 部，索引文件数 848 → 822，正片一个没少。

### 同库内移动文件**不能**拆开已合并的电影

把 Vol.2 挪到独立目录后 rescan，**Plex 会按媒体签名把它重新挂回原条目**，`女性瘾者：第一部` 依然是 2 个版本。

必须**移出媒体库根目录 → rescan → 再移回 → rescan**：

```
mkdir "$STAGING"/.plex-restage                # STAGING 必须在 library root 之外
mv ".../Movies/Nymphomaniac.Volume.2..." .plex-restage/
GET /library/sections/1/refresh                # 等 refreshing/scanning 归 0
mv .plex-restage/Nymphomaniac.Volume.2 ".../Movies/"
GET /library/sections/1/refresh
```

移出后 630 的版本数立刻从 2 降到 1；移回后 Plex 新建了 id=32389，并且**靠目录里的 `.nfo` 自动匹配到了正确的 `女性瘾者：第二部`**，连手动 match 都不需要。

⚠️ 这和剧集的行为**正好相反**：`咒术回战 S2` 移出移回后集映射依然是坏的。所以这一招**只对电影有效**，别指望它能修剧集的集级未匹配。

### rematch 不会更新 `title_sort`，CLP 也不会补救

`title_sort` 是独立字段。把条目 match 到正确的电影后，**旧的拼音排序缩写会留着**，而且 CLP"已处理的项目会被跳过"，所以永远不会自动纠正。本次实测：

| id | 标题（已修正） | 残留的 sort | 应该是 |
| --- | --- | --- | --- |
| 82 | 名侦探柯南：绯色的不在场证明 | `MZTKNFSDZD`（绯色的子弹） | `MZTKNFSDBZCZM` |
| 20214 | 火遮眼 | `NHQHRYZZ`（怒火拳魂荣誉之战） | `HZY` |
| 630 | 女性瘾者：第一部 | `NXYZDEB`（**第二部**的值，和 32389 撞了） | `NXYZDYB` |

修法：先清空再重跑 CLP。

```bash
curl -X PUT "$BASE/library/metadata/<key>?titleSort=&X-Plex-Token=$TOK"   # 200，清空成功
# 然后重跑 chinese-localization-for-plex.py --all
```

**每次 rematch 之后都要检查 `title_sort`**，这一步很容易漏。

### 真正有效的错配检测器（按信噪比排序）

电影库 694 部里只有 3 个真错配。用对了检测器才找得到：

| 检测器 | 命中 | 真错配 | 结论 |
| --- | --- | --- | --- |
| 匹配到的标题本身像幕后花絮（`Visual Effects`/`Making of`/`Interview`/`Featurette`…） | 1 | **1** | 信噪比最高，必做 |
| 标题中文与文件名中文完全不相交 | 17 | **2** | 高信噪比，必做 |
| Plex 年份与文件名年份差 >1 年 | 1 | 0 | 便宜，值得做 |
| 拉丁 token 零重叠 | 42 | **0** | 全是外语片原名 vs 英文文件名，**别追** |

找到的 3 个：

- `蝴蝶效应(导演剪辑版) The.Butterfly.Effect.2004` → 匹配成了 **`Butterfly Effect: Visual Effects`**（幕后特辑）。这种错配 guid 级检查看不出来，因为它确实是个合法的 `plex://movie` guid。
- `名侦探柯南：绯色的不在场证明 Detective.Conan：The Scarlet Alibi 2021` → 匹配成了 **`绯色的子弹`**（The Scarlet Bullet，同年的另一部剧场版）。两个名字只差两个字，极易漏。
- `[火遮眼].The.Furious.2026` → 匹配成了 **`怒火拳魂：荣誉之战`**（同样是 2026 年的另一部动作片）。

### 电影侧已确认**不是**错误的模式（不要动）

- **合集目录**：`[豆瓣 高分100部]/1-50`（50 部）、`Marvel.漫威超级英雄电影22部合集`（23 部）、`Studio.Ghibli.25-Anime.Collection`（25 部）、`哈利波特1-8合集`（8 部，两个不同来源目录）、`X战警十部合集`、`The.Matrix.Trilogy`、`战狼1-2`、`Ted.1-2`。一个目录挂多部**不同的**电影是合集的正常形态。
- **同一部电影的多个压制版本**：`MNHD-FRDS` / `GREENOTEA Top系列` / `MinePAD iPad版` / `BeiTai 4K` / `mUHD-FRDS` 并存是 Plex 多版本功能的正确用法。实测 94 部多版本电影里绝大多数属于这类。
- **同片异名**：`大话西游之大圣娶亲` = `大话西游之仙履奇缘`（都是 Part II）；`指环王1：护戒使者` = `魔戒再现`；`指环王3：王者无敌` = `王者归来`；`玛丽和麦克斯` = `玛丽和马克思`；`网络谜踪` = `网络迷踪`；`寒戰1994` = `寒战1994`（繁简）。
- **外文原名 vs 英文文件名**：`黄金三镖客`↔`The Good the Bad and the Ugly`（orig=`Il buono, il brutto, il cattivo`）、`柏林苍穹下`↔`Wings of Desire`、`四百击`↔`The 400 Blows`、`窃听风暴`↔`The Lives of Others`、`局外人`↔`The Stranger`(orig=`L'Étranger`)。看 `original_title` 是原语种就要放行。
- **短片被单独索引成电影**：`魔术师与兔子`(Presto) 是 WALL-E 的前贴片短片，但它在 TMDB 有独立条目，单独成一条**不算错**。
- **年份不一致但身份正确**：`椒麻堂会` 文件写 2021、TMDB 写 2025（重映/影展日期）。`/matches` 第一名就是当前 guid，说明匹配没错，别改。

### 电影库审计的固定动作

1. 统计每部电影的 `media_items` 数，>1 的逐个看文件名，挑出花絮
2. 跑三个高信噪比检测器（花絮式标题 / 中文不相交 / 年份差）
3. 检查有没有目录里放着**两部不同的电影**
4. 确认 `media_parts.file` 全部存在（本次 0 个失效路径）
5. 修完 rematch 之后，**逐条核对 `title_sort`**，清空再跑 CLP

## 2026-10-27 第四轮：季号错位——最危险的一类，因为它"看起来已经匹配好了"

前三轮都在找 `local://`。**但季号错位产生的条目是带合法 `plex://episode` guid 的**，任何 guid 级检查都会判定它正常，用户却在看错误剧集的错误标题/简介/播出日期。这一轮靠它一次性修掉 124 集。

### 唯一可靠的检测信号：播出日期 vs 文件名里的年份

`斯巴达克斯` 的本地目录是 `Spartacus.Gods.of.the.Arena.2011`，但 Plex 给这些集的元数据是：

```
S2E1  逃亡者    播出 2012-01-27     <- 这是 Vengeance 第 1 集
S3E1  罗马公敌  播出 2013-01-25     <- 这是 War of the Damned 第 1 集
```

**目录写 2011、元数据写 2012**，一眼就错。所以每部剧都要抽 E1 出来，把 `originally_available_at` 和文件名/目录名里的年份对一遍。这比任何字符串相似度都有效。

```sql
select s.[index], e.[index], e.title, e.originally_available_at, mp.file
from metadata_items e
join metadata_items s on e.parent_id = s.id
join media_items mi on mi.metadata_item_id = e.id
join media_parts mp on mp.media_item_id = mi.id
where s.parent_id = ? and e.metadata_type = 4 and e.[index] = 1
order by s.[index];
```

### 必须去 TMDB 核对季结构，绝不能凭直觉推断

从 `<Guid id="tmdb://NNNN">` 拿数字 id（`GET /library/metadata/<key>`，标签在 `Guid` 子节点里，不是 `guid` 属性），再查 `https://www.themoviedb.org/tv/<id>/seasons`。本轮三个实例，**结构各不相同，没有一个能靠猜**：

| 剧 | TMDB id | TMDB 的季结构 | 本地命名 | 正确改法 |
| --- | --- | --- | --- | --- |
| 斯巴达克斯 | 46296 | **S0**=Gods of the Arena(6)、S1=Blood and Sand(13)、S2=Vengeance(10)、S3=War of the Damned(10) | S01–S04 按上映顺序 | S02→**S00**、S03→S02、S04→S03 |
| 纸房子 | 71446 | **S0**=Netflix Part1+2(22)、**S1**=Antena 3 原版(15)、**S2**=Part3+4(16)、**S3**=Part5(10) | S01–S05 = Netflix Part1–5 | Part1→S00E01-13、Part2→S00E14-22、Part3→S02E01-08、Part4→S02E09-16、Part5→S03E01-10 |
| 咒术回战 | 95479 | **绝对编号**：只有 S1，共 59 集；E24=共犯(2021-03-27)、E25=怀玉(2023-07-06) | S01(24) + S02(23) | S02E01-E23→**S01E25-E47** |

`纸房子` 尤其反直觉：TMDB 按西班牙 Antena 3 电视台的原始分季，**根本不按 Netflix 的 Part 编号**，所以本地 S01-S05 和 TMDB S0-S3 之间是错位+合并的关系，必须逐 Part 换算集号偏移量。

`咒术回战` 则是**绝对编号**：整部作品只有 Season 1，第二季的内容是 S1E25 往后。这种番改名时集号要加偏移，不是简单换季号。

### 改名之后的固定收尾：新条目 match 回父剧 guid

改完名 rescan，Plex 会把新命名的目录当成**全新的 unmatched 剧**（本轮出现 `32391 大宋提刑官 Judge of...`、`32488 咒术回战 Jujutsu Kaisen S0...`）。这时 `match` 到父剧的 guid，Plex 会干净地并进去成为正确的季：

```bash
# 大宋提刑官2 的 41 个文件改成 S02Exx 后，把新条目并入 大宋提刑官
curl -X PUT "$BASE/library/metadata/32391/match?guid=$(enc 'plex://show/6a4cd7c809e658d7644e79be')&name=$(enc 大宋提刑官)&year=2005&language=zh-CN&X-Plex-Token=$TOK"
```

实测结果：`大宋提刑官` S1(52) + **S2(41)** 全部 `plex://episode`，真实标题（宋慈深夜进宫 / 湖州洪水现骨玉…）。`咒术回战` S1 从 24 集变 **47 集全匹配**（E24 共犯 2021-03-27、E25 怀玉 2023-07-06、E47 涩谷事变 关门 2023-12-28）。`纸房子` S0(22)/S2(16)/S3(10) 全部匹配，日期与 TMDB 逐集对上。

⚠️ 目录名重排会撞车：`S03→S02` 的目标名要等 `S02→S00` 先执行完才空出来，**必须按目标季号升序处理**。

### 试错了但无效的手段（别再重复）

- **`match` 到同一个 guid 是空操作**。`斯巴达克斯` 已经指着 `5d9c0833...`，再 match 一次什么都不发生。
- **两步重映射**（先 match 到别的条目、再 match 回来）能强制重新识别，S1-S3 会正确恢复，但 **S4 依旧 `local://`**。它能换身份，不能补上抓不到的季。
- **`GET /library/sections/<k>/refresh?force=1`** 返回 200，但行为等同普通扫描，30 秒就结束，**不是"刷新所有元数据"**。
- **`PUT /library/metadata/<k>/refresh?force=1`** 同样无效。
- **没有本地 bundle 缓存可清**：`Metadata/TV Shows/*/*.bundle` 有 468 个，但 `grep -rl <tmdb_id>` **0 命中**——现代 Plex TV agent 直接把 provider 数据写进 DB，不走 bundle 文件。
- `/library/metadata/<k>/unmatch` 用 GET/PUT/OPTIONS 探测**全部 404**（PMS 1.43.3）。API 确实无法 unmatch。

结论：**季号错位只能靠改文件名解决**，所有"重新识别"类的手段都无效。

### 一个解析错误修掉后，可能暴露下一个

`Clannad` 的发行组后缀 `- mawen1250` 被当成第 1250 季，去掉之后**裸年份又被当成了季号**：`Clannad 2007 EP01 [...]` → Season **2007**、`Clannad ~After Story~ 2008 EP01` → Season **2008**，44 集幻影。

原因是 `EPnn` 没有季号，Plex 就往文件名里找季号，找到了年份。**根治办法是把 `EPnn` 直接写成 `SxxEnn`**：

```
Clannad 2007 EP01 [...]                 -> Clannad S01E01 [...]
Clannad ~After Story~ 2008 EP01 [...]   -> Clannad ~After Story~ S02E01 [...]
```

改完 S0(5)/S1(22)/S2(22) 共 49 集全部正确，两个幻影季消失。

注意 `SPnn` 不用动——Plex 认得 `SP` 是 special，会正确归到 Season 0，不会被年份带偏。同理，`.mka` 这类纯音频文件 Plex 视频库不索引，不用跟着改。

### 失效媒体行：另一类"数字对不上"的原因

全库扫描发现 **365 个 episode 媒体行指向已不存在的文件**，且**全部**集中在 `/share/CACHEDEV3_DATA/PT`（该目录的 VarietyShow / Anime / Documentation 现在都是 0 个视频文件，TVSeries 只剩 49 个）。受害最多的是 `非正式会谈`(254)、`中国诗词大会`(50)、`局部`(44)、`王牌对王牌`(12)。

这类行不是匹配错误，是**文件被删了但 Plex 没清库**。API 没有 `/library/sections/<k>/clean`（404），只能让用户在 Web UI：设置 → 媒体库 → **清空回收站**。统计"未匹配集数"时要记得这些行会掺进来。

检测：

```python
for (f,) in con.execute("select mp.file from media_parts mp join media_items mi on mi.id=mp.media_item_id "
                        "join metadata_items e on e.id=mi.metadata_item_id where e.metadata_type=4"):
    if not os.path.exists(f) and not os.path.exists(f.replace("/share/CACHEDEV4_DATA/", "/share/CACHEDEV5_DATA/")):
        ...  # 失效
```

### 本轮净结果

集级未匹配 **355 → 117**（98.95% 已匹配），其中：

- `大宋提刑官2` 41 集 → 并入 `大宋提刑官` S2
- `纸房子` 48 集 → 全部对上 TMDB 的 S0/S2/S3（原先 18 集未匹配 + 17 集元数据是错的）
- `斯巴达克斯` 39 集 → S0/S1/S2/S3 全对（原先 10 集未匹配 + 16 集元数据是错的）
- `咒术回战` 47 集 → 绝对编号 S1E01-E47 全对
- `团子大家族` 44 集幻影 → 清零

剩余 117 集全部核实为上游缺口：`三体`(36，本地是 51 集非官方完整剪辑，TMDB 只有 15 集)、`办公室`(18，Extended 加长版集号超出 TMDB)、`老友记`(8)/`唐顿庄园`(5) 圣诞特辑被编成正片集号、`短剧开始啦`(6) 的 SP 特典、`闪闪的儿科医生`(6，2026 年第四季元数据源尚未收录，该剧只有 tvdb guid 无 tmdb)，以及 26 部剧各 1-3 集的 `SxxE00` 特辑。

## 2026-10-27 第五轮：按内容类型纠正错放 + 按国家重组目录

### 国家目录是「多个 library root」，不是子目录

`电视剧` 的 CN/EN/HK/JP/KR/TW/US/Others **每一个都在 `section_locations` 里单独注册成 library root**：

```
sec2 电视剧  /share/CACHEDEV5_DATA/TVSeries/CN
sec2 电视剧  /share/CACHEDEV5_DATA/TVSeries/EN
...
```

所以给 `动漫/纪录片/综艺` 加国家分层，**必须同时改 section 的 location**，不能只建子目录——否则 Plex 会把 `JP` 当成剧名、把真正的剧名目录当成季。

改 location 的可用写法（`location=` 重复即可，不是 `location[]=`）：

```bash
curl -X PUT "$BASE/library/sections/3?type=show&name=$(enc 动漫)\
&agent=tv.plex.agents.series&scanner=$(enc 'Plex TV Series')&language=zh-CN\
&location=$(enc /share/CACHEDEV2_DATA/Anime/CN)\
&location=$(enc /share/CACHEDEV2_DATA/Anime/JP)\
&location=$(enc /share/CACHEDEV3_DATA/PT/Anime)&X-Plex-Token=$TOK"
```

必须带齐 `type/name/agent/scanner/language`，否则会被清空。改完先 `GET /library/sections` 核对 5 个库的 agent/scanner/location 全部还在，再继续。**动手前务必把 `/library/sections` 的完整 XML 存档**，这是唯一可靠的回滚依据。

注意 `GET /library/sections/<单个id>` 返回的是 `All Shows` 这种 hub，**不是** section 配置；要看配置只能读 `/library/sections` 列表。

### 换 root 会触发全量重新发现，必须准备好重新 match

把 root 从 `Anime` 换成 `Anime/{CN,JP,US}` 之后，Plex 会：

- **发现以前从未索引过的目录**（本轮 `动漫` 从 46 部涨到 60 部）——这是好事
- **同时丢掉一部分原有匹配**，变成 `local://` 新条目（本轮 17 个）

所以顺序必须是：移动文件 → 改 location → rescan → **逐个把 `local://` 条目 match 回原 guid**（同名条目会合并）。原始 guid 要在动手前从 `/library/sections/N/all` 快照下来。

只移动库内单个目录、不改 root 的话，Plex 能靠媒体签名自动跟随，匹配不会丢（`综艺` 试点验证过：6 部全部保住）。**是「换 root」这个动作本身导致重发现**，不是移动文件。

### 重新发现时的自动匹配会挑错条目，必须复核

本轮 rescan 后 Plex 自动匹配出两个错的：

| 目录 | Plex 自动匹配成 | 正确应为 |
| --- | --- | --- |
| `Made.in.Abyss.S02.2022.2160p.B-Global...` | **玛露露库的日常**（来自深渊的衍生短片） | 来自深渊 Season 2 |
| `爱，死亡和机器人S03.Love,Death.and.Robots.2022...` | **爱与死亡**（2023 HBO 真人迷你剧） | 爱、死亡 & 机器人 S3 |

两个都是**同 franchise / 近名碰撞**，和 SKILL 里早就写过的「不能把只重叠一个词当成正确匹配」是同一类错误。rescan 之后**必须重新跑一遍错配检测**，不能假设自动匹配是对的。改法就是 match 到正确 guid，会自动合并（`爱、死亡 & 机器人` 合并后 S1 18 + S2 8 + S3 9 = 35 集，0 local）。

### 分类不能只看 genre 标签，两个方向都会错

**标签会被截断**：`/library/sections/N/all` 返回的 `Genre` 子节点不全（`咒术回战`、`进击的巨人` 在列表里根本没有「动画」标签）。完整标签要读库：

```sql
-- tag_type=1 是 genre，tag_type=8 是 country，tag_type=6 是演员
select tg.metadata_item_id, t.tag from taggings tg join tags t on t.id = tg.tag_id where t.tag_type = 1;
```

用列表标签判定得到「动漫库 15 部不是动画」的荒谬结论；换成 DB 完整标签后是 0 部。

**标签本身也可能是错的**：`蜘蛛侠：英雄归来` 和 `蝴蝶效应` 的 genre 都是 `短片/纪录`，但 summary 明确是正片剧情。这种要靠 summary / original_title 判断真实身份，不能按标签搬库。

**反过来，有「动画」标签也不等于该进动漫库**：`柔美的细胞小将` 是真人韩剧，只有「细胞小镇」段落是动画，标签带「动画」但**应该留在电视剧**（规则是「电视剧只放真人剧集」，它主体就是真人）。`半泽直树` 莫名带了「新闻」标签、`明星大侦探` 是综艺却没有「真人秀」标签——都要靠常识覆盖标签。

结论：**分类结果必须逐条人工过一遍**，本轮 26 个候选里真正该动的只有 6 个。

### 纪录片库是 show 类型，装不下纪录片电影

`纪录片` 的 `type=show`。把 `海豚湾`、`第237号房间` 这类纪录片**电影**移进去，会被当成只有 1 集的剧来刮削。先问用户再动，本轮的选择是**留在电影库**（演唱会电影、奥运开幕式录像同理）。

### 动漫根目录里混进了电影

`Anime/` 下发现两部**电影**，按规则都该进 `电影` 库：

- `Sensei.Would.You.Sit.Beside.Me.2021.1080p.BluRay.x264.DTS-WiKi`（日本**真人**电影，双重错放）
- `Made.in.Abyss.Dawn.of.the.Deep.Soul.2020.1080p.BluRay...`（动画电影）

移到 `Movies/` 后 rescan，两部都**自动匹配正确**（`老师，您能坐在我旁边吗？` 2021 / `来自深渊：深魂的黎明` 2020）。

### 统计未索引文件时必须排除点号目录

第一次统计「未被 Plex 索引的视频文件」得到 2,900 个，其中绝大部分是 `.@__thumb/` 里的缩略图——**缩略图文件名后缀也是 `.mp4`**。排除 `.开头 / @Recycle / @eaDir` 后真实数字是 **1,756**。

这 1,756 个文件分布在 26 个 `动漫` 目录 + 7 个 `纪录片` 目录里，全是 VCB-Studio / Snow-Raws / Moozzi2 这类 BD 合集，命名形如 `[VCB-Studio] Shokugeki no Souma [Ma10p_1080p]/...`，层级深且没有 `SxxEyy`。**把它们移进国家目录并不会让 Plex 开始索引**，这是独立于分类整理的问题，要单独处理（改名或加 `Season 1` 层级）。

其中包含库里完全没有的作品：`[2016][Boku dake ga Inai Machi]`（只有我不在的街道）、`[2019][Nande Koko ni Sensei ga!?]`、`[Nekomoe kissaten][Summer Time Rendering]`（夏日重现）、`[CASO&SumiSora][魔法少女圆香☆魔力]`、`某科学的超电磁炮T`、`守护解放西2`、`脏话史`。

### 先拿最小的库做试点

`综艺` 只有 1 个真实目录，用它完整走一遍「建目录 → 移动 → 改 location → rescan → 核对 6 部全部保住匹配」，确认整套动作安全后再动 `动漫`(103 目录) 和 `纪录片`(59 目录)。这一步省掉了在大库上试错的代价。

### 本轮结果

| 库 | 之前 | 之后 | 变化 |
| --- | --- | --- | --- |
| 电影 | 695 | 697 | +2（从动漫库移入的 2 部电影） |
| 电视剧 | 276 | 271 | −5（4 部动画剧集 + 1 部纪录片剧集移出） |
| 动漫 | 46 | 49 | +4 移入，−1 合并 |
| 纪录片 | 44 | 45 | +1（约翰·威尔逊的十万个怎么做） |
| 综艺 | 6 | 6 | 不变 |

5 个库全部 **0 未匹配**；集级未匹配仍是 117（**重组没有造成任何回归**）；观看记录 391/376 不变。
目录结构：`Anime/{CN 11, JP 68, US 29}`、`Documentation/{CN 19, EN 31, US 12, Others 2}`、`VarietyShow/{CN 1}`，顶层 0 个残留目录、0 个散落视频文件。

## 2026-10-27 第六轮：全 `[方括号]` 目录名会把不相干的作品吞成一部

### 头号陷阱：目录名里**只有** `[...]` 组 → 标题解析为空 → 被邻居吞并

实测：`Anime/JP` 下这四个目录**同时**被并进了同一个 show（自动匹配成 `来自深渊`）：

```text
[Made in Abyss][1080P][CHS-JAP][WEB-MP4]              <- 本体，13 集
[CASO&SumiSora][魔法少女圆香☆魔力][BDRIP][GB_BIG5]        <- 魔法少女小圆，12 集
[Nekomoe kissaten][Summer Time Rendering][1080p][JPSC] <- 夏日重现，12 集
Anime/CN/[Killer Seven][S02][1080P]                   <- 刺客伍六七 S2，10 集
```

结果 `来自深渊` S1 = 13 集却有 **37 个 media_part**、S2 = 12 集却有 **22 个**。
**season/episode 的 guid 全是正确的**（`plex://season/...`），所以「集级未匹配数」完全看不出来——
错的是每一集挂了 2~3 个**别的作品的视频文件**当"多版本"。

根因：Plex 的 scanner 把目录名里所有 `[...]` / `(...)` 组都当作发布组/规格标签剥掉，
剥完**什么都不剩**，四个目录就成了同一个"无名 show"，跨 library root 也能合并（CN + JP 混在一起）。

**注意**：`集数=正确值、part 数=集数的整数倍` 是唯一的强信号。检测脚本必须同时统计
`count(distinct episode)` 和 `count(media_part)`，只数集数会漏掉。

修法：**只改入侵者的目录名**，本体不动（本体已正确匹配，改名会重建条目、丢 watch 状态）：

```text
[CASO&SumiSora][魔法少女圆香☆魔力][BDRIP][GB_BIG5]
  -> 魔法少女小圆.Puella.Magi.Madoka.Magica.S01.2011.BDRip.1080p-CASO&SumiSora
[Nekomoe kissaten][Summer Time Rendering][1080p][JPSC]
  -> 夏日重现.Summer.Time.Rendering.S01.2022.1080p.WEB-DL.JPSC-Nekomoe.kissaten
[Killer Seven][S02][1080P]
  -> 刺客伍六七.S02.2019.1080p.WEB-DL-KillerSeven
```

改完 rescan，`来自深渊` 立刻回到 S1=13/13、S2=12/12；两个新 show 出现但 **unmatched**，
需要手动 match（见下）。

### 排查这类问题的固定脚本

`scripts/absorb_audit.py`：对每个 show 取全部 `media_parts.file`，按 library root 归约出
「show 级目录名」，然后把目录名的 token（含 CJK 2-gram）和 Plex 标题的 token 求交集。
交集为空 = 可疑。**但绝大多数命中是误报**，因为目录用罗马字、Plex 标题用中文：

| 命中 | 实际 |
| --- | --- |
| `[VCB-Studio] SHIROBAKO` vs `白箱` | 同一部 |
| `Kaguya-sama.wa.kokurasetai.S2` vs `辉夜大小姐想让我告白` | 同一部 |
| `Konto.ga.Hajimaru` vs `短剧开始啦` | 同一部 |
| `Unnatural.2018.BluRay` vs `非自然死亡` | 同一部 |
| `The.World.Between.Us.S02.2025` vs `我们与恶的距离` | 真的是第二季 |

所以这个脚本只能当**线索生成器**，每一条都要人工核一遍，别自动改。
真正的判据还是上面那条：**part 数 ≫ 集数，且多出来的 part 来自语义无关的目录**。

### `available=NULL` 的 library root 会让失效条目永远删不掉

`综艺` 库显示 6 部，磁盘上只有 1 部（`VarietyShow/CN/[脱口秀和Ta的朋友们第三季...]`）。
另外 5 部（非正式会谈/局部/明星大侦探/王牌对王牌/中国诗词大会）的文件早在 7 月就删了，
但 rescan 之后依然存在，`emptyTrash` 也报 `About to destroy 0 deleted items`。

原因在 `section_locations.available`：

```sql
select root_path, available, scanned_at from section_locations where library_section_id=5;
-- /share/CACHEDEV2_DATA/VarietyShow/CN   avail=1     scanned=今天
-- /share/CACHEDEV3_DATA/PT/VarietyShow   avail=NULL  scanned=2026-07-05
```

`PT/VarietyShow` 是个**空目录**，Plex 把空 root 当成"可能没挂上盘"，日志里明确写：

```text
* Scanning /share/CACHEDEV3_DATA/PT/VarietyShow
* Marking media not available in /share/CACHEDEV3_DATA/PT/VarietyShow
* Taking 280 items out of the map (which has 280 items) for being unavailable.
```

**"taking out of the map" 是保护，不是删除**——Plex 把这些条目从"待删列表"里摘出去了。
所以 `autoEmptyTrash=True` + `allowMediaDeletion=True` 也不会生效，`deleted_at` 永远是 NULL。

`PUT /library/sections/<id>?...&location=<同样的路径>` **不会**重置这个 flag（Plex 按 path 复用旧行）。
唯一有效的做法是**先摘掉这个 location，再原样加回去**：

```bash
# 1) 只留真实存在内容的 root
curl -X PUT "$BASE/library/sections/5?type=show&name=综艺&agent=tv.plex.agents.series\
&scanner=Plex+TV+Series&language=zh-CN&metadataAgentProviderGroupId=2\
&location=/share/CACHEDEV2_DATA/VarietyShow/CN"
# 2) 等 ~20s，确认只剩 1 部
# 3) 把 PT root 加回去，配置恢复原样
```

实测：5 部幽灵条目立刻消失，`脱口秀和Ta的朋友们` 完好，dead media_parts 380 → 17。
`available` 加回去后仍是 NULL（Plex 只在扫到实际媒体时才置 1），**无害**，它只影响删除保护。

⚠️ 改 section 配置必须带齐 `type/name/agent/scanner/language/metadataAgentProviderGroupId/location`，
少一个就会被清空。改前先备份 `/library/sections`（XML + JSON 各存一份）。
`metadataAgentProviderGroupId`：电影库=1，剧集库=2，别漏。

### refresh / match / emptyTrash 的 HTTP 方法各不相同（全部实测）

| 动作 | 方法 | 路径 |
| --- | --- | --- |
| 扫描库文件 | **GET**（PUT 会 404！） | `/library/sections/<id>/refresh` |
| 重建单项元数据 | **PUT**（GET 会 404） | `/library/metadata/<k>/refresh` |
| 清空回收站 | **PUT** | `/library/sections/<id>/emptyTrash` |
| 应用匹配 | PUT | `/library/metadata/<k>/match?guid=&name=&year=&language=` |

`/library/sections/<id>/refresh`、`/trash`、`/clean` 用 GET 全是 404，
只有 `emptyTrash` 认 PUT。**方法搞错就是 404，body 是一行 HTML，没有任何提示。**

### 等待扫描结束的两个坑

1. `GET /library/sections` 的 JSON 里列表 key 是 **`Directory`**，不是 `Metadata`。
   写成 `Metadata` 会拿到空列表，轮询脚本第一轮就判定"全空闲"直接退出。
2. `/activities` 里真正代表"还在干活"的 job 类型是 **`library.update.item.metadata`**
   和 **`library.update.section`**。只盯 `sections` 的 `refreshing/scanning` 标志会误判——
   实测两个标志全程是 `False/None`，但 `/activities` 里有 3 个 metadata job 跑了 **24 分钟**。
   同时要忽略常驻的 `media.generate.intros` / `media.generate.credits`，它们永远在跑。

### 电影按年份分桶：必须用 Plex 的年份，不能用文件名字面年份

第一版 `movies_year.py` 用 `YEAR.findall(name)[0]` 取**第一个**四位年份，结果：

- `唐探1900.Detective.Chinatown.1900.2025...` → 取到 **1900** → 进了 `19XX/`
- `寒战1994.Cold.War.1994.2026...` → 取到 **1994** → 进了 `19XX/`

标题里的年份被当成了上映年份。这类必须用**元数据年份**兜底。

另外 9 部是「文件名年份 ≠ TMDB 上映年份」，属于系统性偏差，不是 bug：

| 文件 | 文件名年 | Plex 年 | 原因 |
| --- | --- | --- | --- |
| Spider-Man No Way Home.2022.Bluray | 2022 | 2021 | 蓝光发行年 vs 上映年 |
| Marry.My.Dead.Body.2022 | 2022 | 2023 | 影展首映 vs 正式上映 |
| 吉祥如意.The.Reunions.2020 | 2020 | 2021 | 同上 |
| 哆啦A梦 大雄的宇宙小战争 **2021** | 2021 | 2022 | 片名里带年份，疫情延期到 2022 上映 |
| A.New.Old.Play.2021（椒麻堂会） | 2021 | 2025 | Locarno 2021 首映，2025 中国院线 |
| 飞行家.Take.Off.2025 | 2025 | 2026 | 同类型偏差 |

结论：**分桶的依据要和用户在 Plex 里看到的年份一致**，否则"文件夹写着 2022、
详情页写着 2021"会被当成错误。定稿规则 = 用 `metadata_items.year`。

### 移动电影会打断匹配，必须重新 match

把 `唐探1900`×2 和 `寒战1994` 从 `19XX/` 移到正确年份桶后，Plex **没有**按媒体签名重连，
而是建了 3 个全新条目（`local://` guid），电影库瞬间从 698/0未匹配 变成 699/3未匹配。
（之前笔记里说"同库内移动会按签名重连"只对部分情况成立，**不要依赖它**。）

补救很简单，用 `/matches` 搜标题+年份，第一名就是对的：

```bash
apply_match 34627 "plex://movie/6813a4c9f06e10c63c68b098" "寒戰1994" 2026
apply_match 34646 "plex://movie/672f241db8f239b268291c58" "唐探1900" 2025
apply_match 34647 "plex://movie/672f241db8f239b268291c58" "唐探1900" 2025
```

两个 `唐探1900` match 到同一个 guid 后自动合并成"一部两个版本"，条目数回到 698。
**每次批量移动文件后都要重跑一遍 unmatched 统计**，不能假设"只是换个目录"。

`/matches` 的 JSON 里字段是**平铺**的 `name` / `year` / `guid`，
**不在** `Metadata` 子对象下——按 `SearchResult[].Metadata.title` 解析会全部拿到 `None`，
看起来像"搜到了 6 条但全是空的"。

### 一张碟装两部电影时，按年份拆碟而不是整体移动

`Movies/2021/A.New.Old.Play.2021.BluRay...-BMDru/` 里有两部片子：
`椒麻堂会`(A New Old Play, Plex 年 2025) 和 `彩排记`(Ode to Joy, 2008, 邱炯炯)。
整体移动必然有一部进错桶。做法：先把 `Ode.to.Joy.2008.*` 抽到 `200X/Ode.to.Joy.2008.BDRip.../`，
再把剩下的整个碟目录移到 `2025/`（`.Extras`、`.nfo`、`Making.of.*` 跟着主片走）。

### 裸 `Exx` 会被当成 S1，把第二季叠到第一季上

`伍六七之最强发型师.Scissor.Seven.2019...` 是 `刺客伍六七` 的**第二季**，
但文件名是 `...2019.E01....mp4`（无季号），Plex 全部读成 S1，
于是 S1 = 14 集却挂了 38 个 part（S1 两个版本 28 + 最强发型师 10）。
改成 `S02E01..S02E10` 后：S1=10/14、S2=10/10、S3=10/10、S4=10/10。

（S1 那 4 个 unmatched 是 TMDB 只收了 10 集、本地有 14 集，属于上游缺口，不用管。）

### `[12v2]` 这类版本号写法逃过了 `\[(\d{1,3})\]`

`某科学的超电磁炮T` 目录里 24 个文件是 `[To_Aru_Kagaku_no_Railgun_T]S03E01..E25`，
唯独第 12 集叫 `[To_Aru_Kagaku_no_Railgun_T][12v2][720P][GB].mp4`，
`EP = re.compile(r"\[(\d{1,3})\](?!\d)")` 匹配不上 `12v2`，于是它一直没被索引。
**改名脚本跑完必须回头数一遍：目录里的视频文件数 vs Plex 索引到的 part 数**，差多少就是漏了多少。

### DB 读取的几个 schema 事实（都踩过）

```text
media_items      没有 root_file；show 级路径 DB 里根本没有，
                 只能用该剧全部 episode 路径求 os.path.commonpath，
                 再判断 basename 是不是 "Season N"/"Sxx" 决定要不要再上跳一层
tags             tag_type 在 tags 上，不在 taggings 上（tag_type=1 genre, 6 cast, 8 country）
taggings         没有 tag_type 列，只有 (metadata_item_id, tag_id, index, text, ...)
library_sections 没有 location_id；section_type 是整数（1 movie / 2 show / 3 artist / 4 photo）
section_locations 列名是 library_section_id + root_path + available + scanned_at
metadata_item_settings 用 **guid** 关联，没有 metadata_item_id 列
media_parts      有 deleted_at；container 在 media_items 上
c.execute(sql, sid)   -> ProgrammingError: parameters are of unsupported type
                    int 要写成 (sid,)；字符串要写成 (s,)，否则被当序列拆开
```

`metadata_type=12` 是 **clip/extra**，`library_section_id` 为 NULL。
dead media 里如果全是 type 12，那是花絮残留，不影响任何库的条目数，可以忽略。

### 确实没有 TMDB 条目时：报告，不要强修

`Anime/CN/[HDtime].三体.The.Three-Body.Problem.Bilibili.WEB-DL.1080p.H264.AAC/`
有 **51 个文件**，`01.第一集文明入侵.mp4` … `51.第五十一集三体大结局.mp4`，
每集 6~14 分钟，目录时间戳 **2021-12**，覆盖三部曲全部剧情。

它被自动匹配到了 `三体` (2022, `plex://show/636071a0dfcc4ff581107c2f`, 艺画开天/YHKT)。
**这是错配**，三条硬证据：

1. YHKT 版只有 15 集，本地 51 集
2. 集名完全不相交——TMDB 是 `意义之塔/遥远的射手/凡尔纳岛/光锥之内…`，
   本地是 `文明入侵/周文王长夜/人列计算机/大撕裂与远征…`
3. 目录建于 2021-12，**比 YHKT 版首播（2022-12）早整整一年**

判错配时「本地文件时长 vs TMDB 时长」很好用：`metadata_items.duration` 是 TMDB 的（36m），
`media_items.duration` 是本地实测的（8m）。两者差一个数量级 = 肯定不是同一部。

### 我搜错了，条目其实存在：中文「解说版/特别版」要带括号后缀去搜

当时用 `三体 动态漫画` / `三体 漫画` / `我的三体` / `三体 广播剧` /
`Three-Body Problem motion comic` 五个关键词搜遍 `/matches`，判定"TMDB 里没有这个 51 集条目"，
让用户去 Web UI 手动 Unmatch。**这个结论是错的**，用户直接给了 TMDB 链接打脸：

```text
https://www.themoviedb.org/tv/216093  ->  三体（解说版）  2017  1 季 51 集
```

错误原因是**我一直在猜内容的"类型"**（动态漫画？广播剧？），而 TMDB 上它的标题就是
`三体（解说版）`——一个带**全角括号后缀**的普通剧集条目。搜 `三体` 时它排在 12 名开外，
搜 `三体（解说版）` / `三体 解说版` / `三体解说版` / 甚至只搜 `解说版`，它都是**第一名**：

```text
plex://show/6396428c92293724fe7e7a50   三体（解说版）  2017
```

匹配后 51/51 全部命中，动漫库未匹配集数 **49 → 13**，全库 **115 → 79（99.30%）**。

**下次搜不到时的正确升级顺序**（别再跳到最后一步就下"不存在"的结论）：

1. 先搜**光秃秃的主标题**，看前 20 名
2. 再搜**主标题 + 常见中文后缀**：`（解说版）`、`（版）`、`解说版`、`特别版`、`完整版`、
   `剧场版`、`国语版`、`未删减`——B 站/优爱腾的二创和解说在 TMDB 上**是有独立条目的**
3. 再搜**主标题 + 年份**、主标题 + 平台名（Bilibili / 优酷 / 爱奇艺）
4. 还搜不到 → 让用户在 Plex Discover 里翻，从 URL 里拿 key
5. **最后**才是"确实不存在，报告给用户"

判定证据（这次全部对上了）：TMDB 集数 51 = 本地文件数 51；
TMDB 首集播出日 2017-03-30、周更到 2018-03-15；
TMDB 第 2/3/4 集标题 `第二集周文王长夜 / 第三集人列计算机 / 大撕裂与远征`
和本地文件名逐字一致；TMDB 第 1 集标题直接写着 `文曰小强解说版`（B 站 UP 主）。

**`/matches` 的搜索质量高度依赖关键词，"搜不到"几乎从来不等于"不存在"。**
API 确实无法 unmatch（`/library/metadata/<k>/unmatch` 全方法 404），
但**重新 match 到正确的 guid 是完全可行的**，根本不需要 unmatch——
这次就是直接 `PUT /library/metadata/33658/match?guid=plex%3A%2F%2Fshow%2F6396428c...` 覆盖掉的。

### rematch 之后 title_sort 一定要清一次

`三体` → `三体（解说版）` 之后，`title_sort` 还是旧值 `'ST'`，**rematch 不会更新它**，
而 CLP 见到已有值就跳过，所以不会自动补救。手动清一次再跑 CLP：

```bash
curl -X PUT "$BASE/library/metadata/33658?titleSort=&X-Plex-Token=$TOKEN"
PYTHONPATH=$P/pydeps python3 chinese-localization-for-plex.py --all
# -> 三体（解说版） → STJSB
```

库里同时存在 `三体`(2023 电视剧, `ST`) 和 `三体（解说版）`(2017, `STJSB`)，
清完之后两者排序才分得开。

### 本轮净结果

| 指标 | 修之前 | 修之后 |
| --- | --- | --- |
| 电影 | 698 / 0 未匹配 | 698 / **0** 未匹配（中途一度 3 未匹配，已补 match） |
| 动漫 | 57 部 | **59** 部（新增 魔法少女小圆、夏日重现） |
| 综艺 | 6 部（5 部是幽灵） | **1** 部（全部真实存在） |
| 集级未匹配 | 117 | **115**（98.98%） |
| dead media_parts | 380 | **17**（全是 type-12 花絮残留） |
| 观看记录 | 391 行 / 376 已看 | **391 / 376 不变** |
| WRONG LIBRARY / COUNTRY / YEAR BUCKET | 1 / 0 / 9 | **0 / 0 / 0** |

`来自深渊` 从「S1 挂 37 个 part」回到 13/13、12/12；
`刺客伍六七` 补回缺失的 S2；`某科学的超电磁炮` S3 补回第 12 集。

## 本 skill 自带的四个核查脚本（第六轮沉淀）

都直连 Plex 的 SQLite（**只读** `mode=ro`），不写库、不改文件，可以随时反复跑。
DB 路径不用手填：`_config.db()` 会自动发现，也可以用 `PLEX_DB` 显式指定
（详见开头的「路径与环境变量约定」）。

⚠️ QNAP 的 `.qpkg/PlexMediaServer/Resources/` 下也有一个同名 db，那是安装包里
~300KB 的**空壳**，别拿它当数据库。真库 100MB+，在 `Plug-in Support/Databases/`。
`_config.db()` 已按体积自动排除空壳。

| 脚本 | 作用 |
| --- | --- |
| `scripts/plex_snapshot.py <out.json>` | 全库体检：各库 movie/show/season/episode 计数、未匹配数、集级未匹配率、观看记录行数、dead media_parts（按 root 分组）、缺 title_sort 的条目 |
| `scripts/plex_final_verify.py <out.json>` | 结构核查：每个条目是否在**正确的库**、**正确的国家目录**、**正确的年份桶**；输出 WRONG LIBRARY / COUNTRY / YEAR BUCKET / UNMATCHED / DEAD PATHS 六类清单 |
| `scripts/absorb_audit.py` | 找"被吞并"的 show：一部剧的 media_parts 来自多个语义无关的目录 |
| `scripts/plex_wait_idle.py [timeout]` | 阻塞到扫描 + 元数据刷新全部结束，别用 sleep 猜 |

固定跑法（每次大改动前后各跑一遍，diff 就是本轮的净效果）：

```bash
S=<skill-dir>/scripts
python3 $S/plex_snapshot.py       /tmp/snap_before.json
# ... 做改动 ...
curl "$BASE/library/sections/<id>/refresh?X-Plex-Token=$TOKEN"   # 注意是 GET
python3 $S/plex_wait_idle.py 3600
python3 $S/plex_final_verify.py   /tmp/verify_after.json
python3 $S/absorb_audit.py
python3 $S/plex_snapshot.py       /tmp/snap_after.json
```

**`plex_snapshot.py` 的 watch 三项（rows / viewed / rated）必须前后完全一致**，
这是唯一能证明"整理没有破坏用户数据"的指标。第六轮全程 391/376/1 未变。

## 2026-10-27 第七轮：在电影库内部搬目录，匹配一定会掉一部分

### 电影库**不按媒体签名可靠重连**，必须自己留后路

把 17 个合集目录搬进 `Movies/Collections/`（纯目录内移动，没有跨 root），
rescan 之后：**698 部 → 710 部，12 部掉匹配**。

Plex 的行为不是"重连旧条目"，而是**新建一个 `local://` 条目**，旧条目还留着，
于是同一部电影出现两份（一份匹配好、一份未匹配）：

```text
id=229   星际穿越  2014  ok     parts=2   <- 原条目还在
id=34652 032 星际穿越 Interstellar  local  <- 新建的重复条目
```

掉的比例不高（12/825 ≈ 1.5%）但**完全无法预测哪几个会掉**——
上一轮 9 次移动掉了 3 个，这一轮 17 次移动掉了 12 个。所以搬之前必须先做快照。

### 正确做法：搬之前存 basename → guid，搬之后按 guid 精确补回

```bash
S=<skill-dir>/scripts
python3 $S/movie_identity.py /tmp/movie_identity_pre.json   # 搬之前
# ... 移动目录 ...
curl "$BASE/library/sections/1/refresh?X-Plex-Token=$TOKEN"  # GET，不是 PUT
python3 $S/plex_wait_idle.py 1200
python3 $S/movie_move_check.py /tmp/movie_identity_pre.json          # 先看
python3 $S/movie_move_check.py /tmp/movie_identity_pre.json --fix    # 再补
```

关键点是 **用文件名 basename 做键**——移动只改目录、不改文件名，所以 basename 稳定。
快照里记下 `guid / title / year / title_sort / watch`，补匹配时直接
`PUT /library/metadata/<新id>/match?guid=<旧guid>&name=&year=&language=zh-CN`，
**不需要走 `/matches` 搜索，不存在挑错条目的风险**。

两个条目 match 到同一个 `plex://movie/<id>` 后，Plex 会自动合并成"一部多个版本"：
`教父` 从 2 parts 变 4 parts，`盗梦空间` 变 4 parts，总数回到 698、未匹配回到 0。

实测 12/12 全部成功，观看记录 391/376/1 一字未动。

### 电影库**不要**为了子目录去加 location

`Movies/Collections/` 建好之后，section 1 的 location **不需要任何改动**，
547 个媒体分片全部照常索引（总数 825，和搬之前一模一样）。

原因是电影库和剧集库的扫描规则不同：

| | root 的直接子目录被当成 | 需要为子目录注册 location 吗 |
| --- | --- | --- |
| 剧集库（show） | **一部剧** | **要**（国家目录必须各自当 root） |
| 电影库（movie） | 只是普通目录，**递归往下找片子** | **不要** |

而且给电影库加 `Movies/Collections` 这种**嵌套 root** 是有害的：
同一批文件会被 `Movies` 和 `Movies/Collections` 各扫一遍，直接产出几百个重复条目。

判断依据很简单：搬完之后统计各顶层目录的媒体分片数，
如果 `Collections` 有 547、总数仍是 825，就说明单 root 递归扫描完全够用。

### 本轮结果

`Movies/` 顶层从 27 个目录收敛到 **10 个年份桶 + `Collections/`**，0 个散落文件。
`Collections/` 下 17 个合集目录全部有索引。
电影 698 / 未匹配 0，集级未匹配 115（98.98%），观看记录 391/376/1 不变，
WRONG LIBRARY / COUNTRY / YEAR BUCKET / DEAD PATHS 全部 0。
CLP 重跑后 **0 处改动**，说明 1074 个条目的拼音排序和标签汉化都已到位。

## 2026-10-28 第八轮：整季未匹配先查「元数据源是 TVDB 还是 TMDB」

### 症状：整季未匹配 ≠ 上游没收录，很可能只是挂错了源

`闪闪的儿科医生` 第四季 6 集**全部**未匹配。我一开始判定"2026 新季，上游还没收录"，
用户直接甩来 TMDB 链接打脸：`https://www.themoviedb.org/tv/226796` —— **四季，每季 10 集，S4 全都在**。

真正的根因在 `/library/metadata/<rk>` 的 `Guid[]` 子节点里：

```text
改之前: imdb://tt33657029, tvdb://465929      <- 没有 tmdb://！
改之后: tmdb://226796
```

**这部剧的 Plex 条目挂在 TVDB 上，而 TVDB 没有第 4 季；TMDB 有。**
Plex 的 TV agent 同时接 TMDB 和 TVDB，一部剧可能落到任一个源上，
而两个源的季/集收录完整度经常不一样。

### 检测方法：一次把所有受影响剧集的外部 ID 拉出来

```python
d = get("/library/metadata/%s" % ratingKey)["Metadata"][0]
ids = [x["id"] for x in d.get("Guid", [])
       if x["id"].split("://")[0] in ("tmdb", "tvdb", "imdb")]
```

本轮 31 部有未匹配集的剧里，**30 部都有 `tmdb://`，只有这 1 部没有**——
异常值一眼就跳出来了。这应该是"整季未匹配"的**第一个**检查项，
排在"上游缺口"结论之前。

### 修法：换源，不用 unmatch

`/matches` 搜同一个中文标题时，**同名同年经常返回两个候选**：

```text
闪闪的儿科医生  2023  plex://show/6943efb5534c4ddaacff2fcf   <- TVDB 源（当前）
闪闪的儿科医生  2023  plex://show/64695485affa512aa394f122   <- TMDB 源
```

从 `plex://show/<24位hex>` 看不出哪个是 TMDB，**只能试**：match 过去之后
再读一次 `Guid[]`，看有没有 `tmdb://226796`。试错是可逆的（记下旧 guid 就能回退），
而且要先确认这部剧的观看记录数，改完再核对一次没丢。

```bash
PUT /library/metadata/19652/match?guid=plex%3A%2F%2Fshow%2F64695485...&name=闪闪的儿科医生&year=2023&language=zh-CN
PUT /library/metadata/19652/refresh
```

结果 S4 **6/6 全部匹配**，集名 `“窜天娃”历险记 / 人类幼崽侦缉档案 / 奇“渝”π2.0 / 复活甲代理人 /
出厂设置更新中 / 看见·丁达尔` 与 TMDB 逐集对上，观看记录 1 集未丢。
纪录片库未匹配 7 → 1，全库 79 → **73（99.35%）**。

### 「上游缺口」必须逐个查 TMDB 季页面，不能凭集数差推断

这轮把剩下的"疑似缺口"全部拿 TMDB 季页面核了一遍，**又揪出一个误判**：

`是，大臣 S03E08 - Party Games(Christmas.Special)` 我归成了缺口，
实际 TMDB `tv/4549/season/0` 的 **S00E02 就叫「圣诞特辑：党派之戏」(Party Games)**——
改名 `S03E08 → S00E02` 就能修，属于特辑归位问题。

核实后**确认是真缺口**的（本地集数 > TMDB 集数）：

| 剧 | 本地 | TMDB 实收 | TMDB 页 |
| --- | --- | --- | --- |
| 刺客伍六七 S1 | 14 | **10** | tv/79141/season/1 |
| 罗小黑战记 S1 | 43 | **41** | tv/88319/season/1 |
| 约定的梦幻岛 S2 | 12 | **11** | tv/83097/season/2 |
| 猫和老鼠在纽约 S2 | 7 | **6** | tv/127445/season/2 |
| 白箱 S0 | 3 | **2** | tv/64492/season/0 |
| 食戟之灵 S0 | 8 | **6** | tv/62273/season/0 |
| 短剧开始啦 S0 | 6 | **无 S0（404）** | tv/121854/season/0 |
| 9号秘事 S3 | 6 | **5** | tv/61746/season/3 |
| 破产姐妹 S6 | 22 | **21** | tv/39340/season/6 |
| 神烦警探 S8 | 10 | **9** | tv/48891/season/8 |
| 冰冻星球 S1 | 8 | **7** | tv/8724/season/1 |
| 办公室 S4 | 19 | **14** | tv/2316/season/4 |

两个特殊形态要单独记：

1. **TMDB 自己跳号**：`我们这一天` S5 在 TMDB 上是 1 → 3 → 4…，**第 2 集整条缺失**。
   本地 `S05E02` 自然无处可挂。这是 TMDB 的数据洞，不是编号差异。
2. **双集连播被发行版拆开**：办公室 S4 TMDB 收 14 集（`Fun Run`/`Launch Party`/`Money`/
   `Goodbye Toby` 都是一集），本地按发行版拆成 19 个文件；
   老友记文件名直接写着 `S09E23E24`、`S10E17E18`；迷失写着 `S06E17E18`。
   **这类不要改编号**——改了会把两集内容错挂到一集上，比重号更难查。

### 统计未匹配要数「文件」，不能数「集槽位」

之前按 `count(distinct episode)` 统计，`17.3 关于性` 报"1 集未匹配"，
实际上 **9 个文件全被塞进 `S1E17` 一个幻影槽位**（标题里的 `17.3` 劫持了集号解析）；
`白色巨塔` 报"1 集未匹配"，实际 **21 个 `.ts` 全叠在 `S1E2003`**——
文件名 `#[ドラマ]白い巨塔 2003 第01話` 的**字节不是合法 UTF-8**（Shift-JIS 残骸，
`iconv -f UTF-8 -t UTF-8` 直接失败），Plex 读不出 `第01話`，退而抓标题里的 `2003` 当集号。

79 个"未匹配集"背后其实是 **118 个文件**。统计口径要用 `media_parts`，
并且**一个槽位上叠了多个文件 = 扫描器放弃了编号**，这是最严重的一类，必须单独列出来。

### 修特辑归位前，先去 TMDB Season 0 页面对号

本轮已核实、改名即可修的（`SxxEyy → S00Ezz`）：

| 剧 | 本地 | TMDB 实际 |
| --- | --- | --- |
| 唐顿庄园 | S02E09 Christmas 2011 | S00E02 |
| 唐顿庄园 | S03E09 Christmas 2012 | S00E03 |
| 唐顿庄园 | S04E09 Christmas 2013 | S00E05 |
| 唐顿庄园 | S05E09 Christmas 2014 | S00E06 |
| 唐顿庄园 | S06E09 Christmas 2015 | S00E11 |
| IT狂人 | S05E01 (2013) | S00E01 最后一节：因特网的逆袭 |
| 黑镜 | S03E00 White Christmas | **S02E04**（黑镜没有 Season 0，页面 404） |
| 神探夏洛克 | S01E00 | S00E01 Unaired Pilot |
| 命运石之门 | S01E25 | S00E01 Egoistic Poriomania |
| 是，大臣 | S03E08 Party Games | S00E02 圣诞特辑：党派之戏 |

⚠️ **别假设特辑一定进 Season 0**：黑镜的 White Christmas 在 TMDB 上是 **S02E04**，
因为黑镜压根没有 S0。每部都得单独开一次 TMDB 页面确认。

花絮类（`兄弟连(花絮)/` 里的 We Stand Alone Together、The Making of；
`剧终幕后特辑…S12E25.Extras`）不该进 Season 0，
应该把目录改成 `.Extras`（点号开头），Plex 会自动从剧集列表里摘掉——
和电影库那轮处理 extras 的手法一致。

### 本轮结果

| 指标 | 之前 | 之后 |
| --- | --- | --- |
| 集级未匹配 | 115 | **73（99.35%）** |
| 纪录片库未匹配 | 7 | **1** |
| 动漫库未匹配 | 49 | **13**（三体解说版 36 集） |
| 观看记录 | 391/376/1 | **391/376/1 不变** |

**两次被用户用 TMDB 链接纠正**（三体解说版 `tv/216093`、闪闪的儿科医生 `tv/226796`），
两次的共同错误都是：**搜索/推断没穷尽就下"上游没有"的结论**。
下次遇到未匹配，顺序固定为：查 `Guid[]` 有没有 `tmdb://` → 搜标题的各种后缀变体 →
开 TMDB 季页面数集数 → 最后才允许说"上游缺口"。

### 执行结果：`scripts/round8_rename.py`，72 项，73 → 54

改名前必须先跑 `scripts/show_identity.py` 存 show + episode 两级 guid 快照；
改完 rescan 再用 `scripts/unmatched_files.py` 对比。本轮**没有一部剧掉匹配**
（17.3 关于性 连目录都改了，Plex 仍按媒体签名重连回原条目，不需要补 match）。

| 剧 | 改动 | 结果 |
| --- | --- | --- |
| 白色巨塔 | 21 个 `.ts` + 21 个 `.srt` 全部重命名成 `白い巨塔.2003.S01Exx.HDTV-TS` | S1:21 全匹配（原来 21 个文件全叠在 `S1E2003`） |
| 17.3 关于性 | 目录 `17.3 关于性 17.3 about a sex （全9集）2020` → `关于性.17.3.about.a.Sex.S01.2020.1080p.WEB-DL`；文件 `EPxx` → `S01Exx` | S1:9 全匹配（原来 9 个文件全叠在 `S1E17`） |
| 唐顿庄园 | 5 个圣诞特辑 `S02E09/S03E09/S04E09/S05E09/S06E09` → `S00E02/E03/E05/E06/E11` | S0:5，S1:7，S2-S6 各 8，与 TMDB 完全一致 |
| 黑镜 | `S03E00.Special.White.Christmas` → `S02E04` | S2:4（马上回来/白熊/沃尔多一刻/白色圣诞节） |
| 兄弟连 | 花絮目录 `兄弟连(花絮)...` → `Season 00`；`E01.We.Stand.Alone` → `S00E04`、`E02.The.Making` → `S00E03` | S0:2 + S1:10，幻影 S3 消失 |
| IT狂人 / 神探夏洛克 / 是，大臣 / 半泽直树 / 摩斯探长前传 / 9号秘事 / 流星花园 / 命运石之门 | `SxxE00` → `S00Eyy`（命运石之门另建 `Season 00` 目录并把 E25 移进去） | 各自 S0 建好，全部匹配 |
| 生活大爆炸 | `剧终幕后特辑...S12E25.Extras` 的 `.mkv`+`.jpg` 移进 `.Extras/` | 从剧集列表摘掉 |

两个要点：

1. **文件名非法 UTF-8 会让 Plex 抓错集号**。`白色巨塔/` 里 21 个 `.ts` 的字节
   不是合法 UTF-8（Shift-JIS 残骸，`iconv -f UTF-8 -t UTF-8` 直接失败），
   `第01話` 里的 `話` 变成无效字节，Plex 读不出集号，退而抓标题里的 `2003`，
   21 个文件全叠进 `S1E2003` 一个槽位。重命名成合法 UTF-8 后立刻正常。
   **检测手段**：`os.listdir()` 返回的 str 能否 `.encode("utf-8")`，
   或用 `iconv` 逐个校验。注意别用 shell 的 `while read` 测——locale 会造成假阳性。

2. **目录名以数字开头会劫持集号解析**。`17.3 关于性 17.3 about a sex （全9集）2020/`
   里的 `17.3 关于性.EP01.mp4`…`EP09.mp4`，9 个文件全被塞进 `S1E17`。
   光给文件加 `S01Exx` 不够，**目录名也要改**，把开头的 `17.3` 挪到后面：
   `关于性.17.3.about.a.Sex.S01.2020.1080p.WEB-DL/`。

### 剩下 54 个槽位 / 65 个文件 / 18 部剧，逐个查过 TMDB 季页面

**真上游缺口（39 槽位）**——本地集数 > TMDB 收录数：

| 剧 | 本地 | TMDB | 未匹配 |
| --- | --- | --- | --- |
| 刺客伍六七 S1 | 14 | 10 | E11-E14 |
| 办公室 S3/S4/S5/S6/S7/S9 | 各季多 2-5 | S4=14 | 18 槽位 |
| 短剧开始啦 S0 | 6 | **无 S0（404）** | E1-E6 |
| 罗小黑战记 S1 | 43 | 41 | E42/E43 |
| 食戟之灵 S0 | 8 | 6 | E7/E8 |
| 约定的梦幻岛 S2 | 12 | 11 | E12 |
| 猫和老鼠在纽约 S2 | 7 | 6 | E7 |
| 白箱 S0 | 3 | 2 | E3 |
| 9号秘事 S3 | 6 | 5 | E6 |
| 破产姐妹 S6 | 22 | 21 | E22 |
| 神烦警探 S8 | 10 | 9 | E10 |
| 冰冻星球 S1 | 8 | 7 | E8 |
| 我们这一天 S5 | 16 | **TMDB 自己跳号 1→3**，缺 E2 | E2 |

**双集连播拆分（11 槽位）**：老友记（文件名自带 `S09E23E24`、`S10E17E18`）、
迷失（`S06E17E18`）。**不要改编号**——改了会把两集内容错挂到一集上。

**故意跳过、无法判定（3 槽位）**：TMDB 有多个候选特辑，本地文件给不出区分依据。
这种要老实留着，别硬塞：

- 摩登家庭 `S11E00`（42 分钟；TMDB S0 有 32 个候选）
- 窥探 `S01E00` ×2（59 分钟 "Restart Highlight Clips"；TMDB S0 有 回顾特辑1/2）
- 命运石之门 `S01E00`（4分30秒；TMDB S00E02-E05 是四个时长相近的短片）









