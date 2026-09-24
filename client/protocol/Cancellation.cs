namespace BatterMC.Protocol;

/// <summary>
/// 区分"我们自己要退出"和"某个操作被超时掐了"。
///
/// 这两件事在 .NET 里长得一模一样：<see cref="HttpClient"/> 的超时抛
/// <c>TaskCanceledException</c>，而它是 <see cref="OperationCanceledException"/> 的子类。
/// 于是下面这个写法看着合理，实际上是个陷阱：
///
/// <code>
/// while (!ct.IsCancellationRequested)
/// {
///     try { await 一个带超时的 HTTP 请求(); }
///     catch (Exception error) when (error is not OperationCanceledException)
///     {
///         记一行日志继续转;      // ← 超时根本走不到这里
///     }
/// }
/// </code>
///
/// 超时被过滤器放过去，异常冲出整个 while——**一次网络抖动就让这条循环永久停摆，
/// 直到进程重启**。而进程还活着、别的循环还在转，所以现场看起来完全正常。
///
/// 实测过：重启控制面容器之后 agent 的上报循环就停了，控制面显示"服务端在线=False"、
/// 客户端拿到"服务器侧没有可用的打洞候选"直接退回中转，而 agent 日志里最后一条上报
/// 之后什么都没有，端口映射循环照常每 20 分钟打一行。这个仓库里同样的过滤器写法
/// 有二十多处，长循环里的那几处都修成了用这个判断。
/// </summary>
public static class Cancellation
{
    /// <summary>
    /// 这个异常是不是"我们自己要退出"——只有取消令牌真的被取消了才算。
    ///
    /// 用在异常过滤器里：<c>catch (Exception error) when (!Cancellation.IsShutdown(error, ct))</c>。
    /// 超时、DNS 失败、502 之类都会被接住并继续循环；只有真正的关停才让循环退出。
    /// </summary>
    public static bool IsShutdown(Exception error, CancellationToken ct)
        => error is OperationCanceledException && ct.IsCancellationRequested;
}
