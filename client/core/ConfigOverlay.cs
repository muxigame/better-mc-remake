using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using BatterMC.Protocol;

namespace BatterMC.Core;

public sealed record OverlayResult(string Path, int Changed, int Total, bool Created, string? Error);

/// <summary>
/// 硬配置同步引擎。
///
/// 规则很简单但必须严格：清单里点名的键，每次启动强制写成服务器的值；
/// 没点名的键，原样保留玩家改过的内容。所以这里不能"整文件覆盖"，
/// 必须做键级别的最小改动，并且尽最大努力保留注释和排版
/// —— NeoForge 的 toml 配置注释里全是取值范围说明，冲掉玩家就没法调了。
/// </summary>
public static class ConfigOverlay
{
    public static List<OverlayResult> ApplyAll(IEnumerable<ConfigOverlaySpec> specs, LauncherPaths paths)
    {
        var results = new List<OverlayResult>();
        foreach (var spec in specs)
        {
            try { results.Add(Apply(spec, paths)); }
            catch (Exception ex)
            {
                Log.Warn($"硬配置写入失败 {spec.Path}：{ex.Message}");
                results.Add(new OverlayResult(spec.Path, 0, spec.Enforce.Count, false, ex.Message));
            }
        }
        return results;
    }

    public static OverlayResult Apply(ConfigOverlaySpec spec, LauncherPaths paths)
    {
        var file = paths.ResolveGameFile(spec.Path);
        var existed = File.Exists(file);
        var total = spec.Enforce.Count + spec.RemoveFromList.Sum(kv => kv.Value.Count);

        if (!existed && !spec.CreateIfMissing)
            return new OverlayResult(spec.Path, 0, total, false, null);

        var original = existed ? File.ReadAllText(file) : "";

        string updated;
        int changed;
        switch (spec.Format)
        {
            case OverlayFormat.Properties:
                updated = ApplyProperties(original, spec.Enforce, out changed);
                updated = RemoveListEntries(updated, spec.RemoveFromList, out var removed);
                changed += removed;
                break;
            case OverlayFormat.Json:
                updated = ApplyJson(original, spec.Enforce, out changed); break;
            case OverlayFormat.Toml:
                updated = ApplyToml(original, spec.Enforce, out changed); break;
            default:
                updated = original; changed = 0; break;
        }

        if (!existed || !string.Equals(original, updated, StringComparison.Ordinal))
        {
            AtomicFile.WriteAllText(file, updated);
            Log.Info($"硬配置 {spec.Path}：纠正 {changed}/{total} 项（{(existed ? "更新" : "新建")}）");
        }

        return new OverlayResult(spec.Path, changed, total, !existed, null);
    }

    // ---------------------------------------------------------------- properties

    /// <summary>
    /// 逐行 key=value / key:value。
    /// options.txt 用冒号，iris.properties 之类用等号，所以分隔符从文件内容里嗅探。
    /// </summary>
    public static string ApplyProperties(string content, Dictionary<string, JsonElement> enforce, out int changed)
    {
        changed = 0;
        var sep = SniffSeparator(content);
        var newline = DetectNewline(content);
        var lines = SplitLines(content);
        var remaining = new Dictionary<string, JsonElement>(enforce, StringComparer.Ordinal);

        for (var i = 0; i < lines.Count; i++)
        {
            var line = lines[i];
            var trimmed = line.TrimStart();
            if (trimmed.Length == 0 || trimmed[0] == '#' || trimmed[0] == '!') continue;

            var idx = line.IndexOf(sep);
            if (idx <= 0) continue;

            var key = line[..idx].Trim();
            if (!remaining.TryGetValue(key, out var value)) continue;

            var desired = ScalarToPlainString(value);
            var rebuilt = line[..idx] + sep + desired;
            if (!string.Equals(rebuilt, line, StringComparison.Ordinal)) changed++;
            lines[i] = rebuilt;
            remaining.Remove(key);
        }

        // 文件里没有的键追加到末尾
        if (remaining.Count > 0)
        {
            if (lines.Count > 0 && lines[^1].Trim().Length != 0) { /* 保持紧凑，不额外空行 */ }
            foreach (var (k, v) in remaining)
            {
                lines.Add(k + sep + ScalarToPlainString(v));
                changed++;
            }
        }

        return string.Join(newline, lines);
    }

    /// <summary>
    /// 从列表型的值里摘掉点名的条目，其余部分一个字符都不动。
    ///
    /// 只认 options.txt 这种「值本身是 JSON 数组」的键，典型就是 resourcePacks。
    /// 不重新序列化整个数组：Minecraft 用 Gson 写这行，中文会写成 \uXXXX，
    /// 我们再序列化一遍就会跟它来回打架，每次启动都判定成有改动。
    /// </summary>
    public static string RemoveListEntries(
        string content, Dictionary<string, List<string>> removals, out int changed)
    {
        changed = 0;
        if (removals.Count == 0) return content;

        var sep = SniffSeparator(content);
        var newline = DetectNewline(content);
        var lines = SplitLines(content);

        for (var i = 0; i < lines.Count; i++)
        {
            var line = lines[i];
            var trimmed = line.TrimStart();
            if (trimmed.Length == 0 || trimmed[0] == '#' || trimmed[0] == '!') continue;

            var idx = line.IndexOf(sep);
            if (idx <= 0) continue;

            var key = line[..idx].Trim();
            if (!removals.TryGetValue(key, out var drop) || drop.Count == 0) continue;

            var value = line[(idx + 1)..];
            // 不是数组就别碰，免得把玩家改坏的行搅得更烂
            if (!value.TrimStart().StartsWith('[')) continue;

            foreach (var entry in drop)
            {
                while (true)
                {
                    value = DropArrayToken(value, entry, out var removed);
                    if (!removed) break;
                    changed++;
                }
            }

            lines[i] = line[..(idx + 1)] + value;
        }

        return string.Join(newline, lines);
    }

    /// <summary>删掉数组文本里的一个 "条目"，连同它相邻的一个逗号。</summary>
    private static string DropArrayToken(string arrayText, string entry, out bool removed)
    {
        removed = false;
        var token = "\"" + entry + "\"";
        var at = arrayText.IndexOf(token, StringComparison.Ordinal);
        if (at < 0) return arrayText;

        var start = at;
        var end = at + token.Length;
        if (end < arrayText.Length && arrayText[end] == ',') end++;
        else if (start > 0 && arrayText[start - 1] == ',') start--;

        removed = true;
        return arrayText[..start] + arrayText[end..];
    }

    private static char SniffSeparator(string content)
    {
        foreach (var raw in content.Split('\n'))
        {
            var line = raw.TrimStart();
            if (line.Length == 0 || line[0] is '#' or '!') continue;
            var colon = line.IndexOf(':');
            var equals = line.IndexOf('=');
            if (colon >= 0 && (equals < 0 || colon < equals)) return ':';
            if (equals >= 0) return '=';
        }
        return '=';
    }

    // ---------------------------------------------------------------- json

    /// <summary>
    /// JSON：用 JsonNode 定位并赋值。键路径用 '/' 分层，例如 "quality/fog_quality"。
    /// 中间层不存在会自动建对象。注意 JSON 不保留注释，所以只对纯 JSON 配置使用。
    /// </summary>
    public static string ApplyJson(string content, Dictionary<string, JsonElement> enforce, out int changed)
    {
        changed = 0;
        JsonObject root;
        try
        {
            root = string.IsNullOrWhiteSpace(content)
                ? new JsonObject()
                : JsonNode.Parse(content, documentOptions: new JsonDocumentOptions
                {
                    CommentHandling = JsonCommentHandling.Skip,
                    AllowTrailingCommas = true,
                })!.AsObject();
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"JSON 解析失败：{ex.Message}", ex);
        }

        foreach (var (path, value) in enforce)
        {
            var segments = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
            if (segments.Length == 0) continue;

            var node = root;
            for (var i = 0; i < segments.Length - 1; i++)
            {
                if (node[segments[i]] is JsonObject child) node = child;
                else { var created = new JsonObject(); node[segments[i]] = created; node = created; }
            }

            var leaf = segments[^1];
            var desired = JsonNode.Parse(value.GetRawText());
            var before = node[leaf]?.ToJsonString();
            var after = desired?.ToJsonString();
            if (!string.Equals(before, after, StringComparison.Ordinal)) changed++;
            node[leaf] = desired;
        }

        return root.ToJsonString(new JsonSerializerOptions
        {
            WriteIndented = true,
            Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        });
    }

    // ---------------------------------------------------------------- toml

    /// <summary>
    /// TOML：按行做外科手术。不做完整解析、不重新序列化，
    /// 这样注释、空行、排版、甚至玩家自己加的奇怪东西全都原样保留。
    ///
    /// 键路径 "client/fogDistance" = 表 [client] 下的 fogDistance。
    /// 顶层键直接写 "fogDistance"。多层表写 "a/b/key" = [a.b] 下的 key。
    /// </summary>
    public static string ApplyToml(string content, Dictionary<string, JsonElement> enforce, out int changed)
    {
        changed = 0;
        var newline = DetectNewline(content);
        var lines = SplitLines(content);
        var remaining = new Dictionary<string, JsonElement>(enforce, StringComparer.Ordinal);

        // 第一遍：就地替换已存在的键
        var currentTable = "";
        for (var i = 0; i < lines.Count; i++)
        {
            var line = lines[i];
            var trimmed = line.Trim();

            if (trimmed.StartsWith('[') && trimmed.EndsWith(']'))
            {
                currentTable = trimmed.Trim('[', ']').Trim();
                continue;
            }
            if (trimmed.Length == 0 || trimmed[0] == '#') continue;

            var eq = IndexOfTopLevelEquals(line);
            if (eq <= 0) continue;

            var key = line[..eq].Trim().Trim('"', '\'');
            var full = currentTable.Length == 0 ? key : currentTable.Replace('.', '/') + "/" + key;

            if (!remaining.TryGetValue(full, out var value)) continue;

            var indent = line[..(line.Length - line.TrimStart().Length)];
            var desired = $"{indent}{line[indent.Length..eq].TrimEnd()} = {TomlLiteral(value)}";

            // 多行数组：把后续行一并吃掉
            var consumeTo = i;
            if (IsUnbalancedArrayStart(line, eq))
            {
                var depth = BracketDepth(line[(eq + 1)..]);
                while (depth > 0 && consumeTo + 1 < lines.Count)
                {
                    consumeTo++;
                    depth += BracketDepth(lines[consumeTo]);
                }
            }

            if (consumeTo > i) lines.RemoveRange(i + 1, consumeTo - i);
            if (!string.Equals(lines[i], desired, StringComparison.Ordinal)) changed++;
            lines[i] = desired;
            remaining.Remove(full);
        }

        if (remaining.Count == 0) return string.Join(newline, lines);

        // 第二遍：把还没落地的键插进对应的表；表不存在就在末尾新建
        foreach (var group in remaining.GroupBy(kv => TableOf(kv.Key)))
        {
            var table = group.Key;
            var insertAt = FindTableEnd(lines, table);

            if (insertAt < 0)
            {
                if (lines.Count > 0 && lines[^1].Trim().Length != 0) lines.Add("");
                if (table.Length > 0) lines.Add($"[{table}]");
                insertAt = lines.Count;
            }

            foreach (var kv in group)
            {
                var leaf = kv.Key.Contains('/') ? kv.Key[(kv.Key.LastIndexOf('/') + 1)..] : kv.Key;
                lines.Insert(insertAt++, $"{TomlKey(leaf)} = {TomlLiteral(kv.Value)}");
                changed++;
            }
        }

        return string.Join(newline, lines);
    }

    /// <summary>
    /// 裸键只能用 A-Z a-z 0-9 _ -，别的必须加引号。
    /// NeoForge 的配置键经常带空格（"enable mod ui"），直接裸写会让整份配置解析失败。
    /// </summary>
    private static string TomlKey(string leaf)
    {
        if (leaf.Length > 0 && leaf.All(c => char.IsAsciiLetterOrDigit(c) || c == '_' || c == '-'))
            return leaf;
        return "\"" + EscapeToml(leaf) + "\"";
    }

    private static string TableOf(string path)
    {
        var idx = path.LastIndexOf('/');
        return idx < 0 ? "" : path[..idx].Replace('/', '.');
    }

    /// <summary>返回指定表最后一行的下一个位置；表不存在返回 -1。</summary>
    private static int FindTableEnd(List<string> lines, string table)
    {
        var header = "[" + table + "]";
        var start = -1;
        for (var i = 0; i < lines.Count; i++)
        {
            var t = lines[i].Trim();
            if (table.Length == 0)
            {
                // 顶层：插在第一个表头之前
                if (t.StartsWith('[') && t.EndsWith(']')) return i;
                continue;
            }
            if (string.Equals(t, header, StringComparison.Ordinal)) { start = i; break; }
        }
        if (table.Length == 0) return lines.Count;
        if (start < 0) return -1;

        var end = lines.Count;
        for (var i = start + 1; i < lines.Count; i++)
        {
            var t = lines[i].Trim();
            if (t.StartsWith('[') && t.EndsWith(']')) { end = i; break; }
        }
        // 回退掉表尾的空行，插在真正的最后一项之后
        while (end - 1 > start && lines[end - 1].Trim().Length == 0) end--;
        return end;
    }

    /// <summary>找到不在引号内的第一个 '='。</summary>
    private static int IndexOfTopLevelEquals(string line)
    {
        var inSingle = false; var inDouble = false;
        for (var i = 0; i < line.Length; i++)
        {
            var c = line[i];
            if (c == '\'' && !inDouble) inSingle = !inSingle;
            else if (c == '"' && !inSingle) inDouble = !inDouble;
            else if (c == '#' && !inSingle && !inDouble) return -1;
            else if (c == '=' && !inSingle && !inDouble) return i;
        }
        return -1;
    }

    private static bool IsUnbalancedArrayStart(string line, int eq)
        => BracketDepth(line[(eq + 1)..]) > 0;

    private static int BracketDepth(string s)
    {
        var depth = 0; var inSingle = false; var inDouble = false;
        foreach (var c in s)
        {
            if (c == '\'' && !inDouble) inSingle = !inSingle;
            else if (c == '"' && !inSingle) inDouble = !inDouble;
            else if (c == '#' && !inSingle && !inDouble) break;
            else if (!inSingle && !inDouble)
            {
                if (c == '[') depth++;
                else if (c == ']') depth--;
            }
        }
        return depth;
    }

    /// <summary>把 JSON 值转成 TOML 字面量。</summary>
    public static string TomlLiteral(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.String => "\"" + EscapeToml(e.GetString() ?? "") + "\"",
        JsonValueKind.True => "true",
        JsonValueKind.False => "false",
        JsonValueKind.Number => e.GetRawText(),
        JsonValueKind.Null => "\"\"",
        JsonValueKind.Array => "[" + string.Join(", ", e.EnumerateArray().Select(TomlLiteral)) + "]",
        _ => "\"" + EscapeToml(e.GetRawText()) + "\"",
    };

    private static string EscapeToml(string s)
    {
        var sb = new StringBuilder(s.Length + 8);
        foreach (var c in s)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                default: sb.Append(c); break;
            }
        }
        return sb.ToString();
    }

    // ---------------------------------------------------------------- shared

    /// <summary>properties 里所有值都是裸文本，字符串不加引号。</summary>
    public static string ScalarToPlainString(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.String => e.GetString() ?? "",
        JsonValueKind.True => "true",
        JsonValueKind.False => "false",
        JsonValueKind.Number => e.GetRawText(),
        JsonValueKind.Null => "",
        // options.txt 里的列表本来就是 JSON 数组形式，原样输出
        _ => e.GetRawText(),
    };

    private static List<string> SplitLines(string content)
        => content.Replace("\r\n", "\n").Replace('\r', '\n').Split('\n').ToList();

    private static string DetectNewline(string content)
        => content.Contains("\r\n", StringComparison.Ordinal) ? "\r\n" : "\n";
}
