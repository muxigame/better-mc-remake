using System.Diagnostics;

namespace BatterMC.Core;

/// <summary>Wait for an owned installer; do not orphan it on cancel/timeout.</summary>
public static class ChildProcessLifetime
{
    public static async Task WaitAsync(Process process, TimeSpan timeout, CancellationToken ct)
    {
        using var deadline = new CancellationTokenSource(timeout);
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct, deadline.Token);
        try
        {
            await process.WaitForExitAsync(linked.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (linked.IsCancellationRequested)
        {
            try
            {
                if (!process.HasExited) process.Kill(entireProcessTree: true);
                await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5)).ConfigureAwait(false);
            }
            catch (Exception error)
            {
                throw new IOException("无法终止安装进程，请关闭该安装进程后重试", error);
            }
            if (ct.IsCancellationRequested) throw new OperationCanceledException(ct);
            throw new TimeoutException("安装等待超时，安装进程已终止");
        }
    }
}
