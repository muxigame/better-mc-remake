namespace BatterMC.Core;

/// <summary>
/// Reports on the caller's thread, so a completed download cannot enqueue a
/// stale progress event after the next install phase or terminal state.
/// Callbacks must be thread-safe (the RPC event writer serializes output).
/// </summary>
internal sealed class InlineProgress<T>(Action<T> report) : IProgress<T>
{
    public void Report(T value) => report(value);
}
