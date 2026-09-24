using System.Diagnostics;
using System.Text;

namespace BatterMC.Core;

/// <summary>
/// 记录一连串阶段各花了多久，最后打成一行。
///
/// 存在的理由：连接建立的总时长是玩家唯一感知得到的指标，而它是十几个步骤叠出来的
/// ——有几步是网络时延，有几步是我们自己写死的等待。只看总数没法判断该优化哪里，
/// 而日志里逐行的时间戳要人肉相减，跨行还容易看错。
///
/// 报**相邻两个标记之间的增量**，不是从头算的累计值：要找的是"哪一步慢"，累计值
/// 会把前面所有步骤的时间都算进来，反而掩盖问题。
/// </summary>
public sealed class PhaseTimer
{
    private readonly Stopwatch _watch = Stopwatch.StartNew();
    private readonly List<(string Name, TimeSpan At)> _marks = [];

    public void Mark(string name) => _marks.Add((name, _watch.Elapsed));

    public TimeSpan Total => _watch.Elapsed;

    /// <summary>一行时间线：`阶段 耗时 → 阶段 耗时 → …（合计 x.xx s）`。</summary>
    public string Describe(string? label = null)
    {
        var text = new StringBuilder(label is null ? "" : label + " ");
        var previous = TimeSpan.Zero;
        for (var i = 0; i < _marks.Count; i++)
        {
            var (name, at) = _marks[i];
            if (i > 0) text.Append(" → ");
            text.Append(name).Append(' ').Append($"{(at - previous).TotalMilliseconds:0}ms");
            previous = at;
        }
        if (_marks.Count > 0) text.Append('，');
        text.Append($"合计 {Total.TotalMilliseconds:0}ms");
        return text.ToString();
    }
}
