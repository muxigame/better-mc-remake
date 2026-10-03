using System.IO.Pipes;
using System.Security.Cryptography;
using System.Text;

namespace BatterMC.Core;

/// <summary>One game's native-only renewal channel. The original account access token is passed only through the same-user native pipe.</summary>
public sealed class TerminalCredentialBroker : IAsyncDisposable
{
    private readonly Func<CancellationToken, Task<string?>> _mint;
    private readonly CancellationTokenSource _stop = new();
    private readonly Task _worker;
    private int _disposed;
    public string PipeName { get; } = "muxi-terminal-" + Guid.NewGuid().ToString("N");
    public string Secret { get; } = Convert.ToBase64String(RandomNumberGenerator.GetBytes(32)).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    public override string ToString() => "TerminalCredentialBroker[credentials=<redacted>]";

    public TerminalCredentialBroker(Func<CancellationToken, Task<string?>> mint)
    {
        _mint = mint;
        _worker = RunAsync();
    }

    private async Task RunAsync()
    {
        while (!_stop.IsCancellationRequested)
        {
            try
            {
                // Current Windows user/elevation only; never a remotely addressable pipe.
                await using var pipe = new NamedPipeServerStream(PipeName, PipeDirection.InOut, 1,
                    PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                await pipe.WaitForConnectionAsync(_stop.Token).ConfigureAwait(false);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(_stop.Token);
                timeout.CancelAfter(TimeSpan.FromSeconds(6));
                var request = new byte[44];
                await pipe.ReadExactlyAsync(request, timeout.Token).ConfigureAwait(false);
                var expected = Encoding.ASCII.GetBytes(Secret);
                var valid = request[43] == 10 && CryptographicOperations.FixedTimeEquals(request.AsSpan(0, 43), expected);
                Array.Clear(request); Array.Clear(expected);
                var credential = valid ? await _mint(timeout.Token).WaitAsync(timeout.Token).ConfigureAwait(false) : null;
                var response = Encoding.ASCII.GetBytes((TerminalCredentialEnvironment.ValidAccessToken(credential) ? credential : "") + "\n");
                try { await pipe.WriteAsync(response, timeout.Token).ConfigureAwait(false); await pipe.FlushAsync(timeout.Token).ConfigureAwait(false); }
                finally { Array.Clear(response); }
            }
            catch (Exception) when (!_stop.IsCancellationRequested)
            {
                // Pipe errors and issuer failures contain no public diagnostic body.
                await Task.Delay(100, _stop.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (_stop.IsCancellationRequested) { return; }
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
        _stop.Cancel();
        try { await _worker.ConfigureAwait(false); } catch (OperationCanceledException) { }
        _stop.Dispose();
    }
}
