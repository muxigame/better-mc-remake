using System.Diagnostics;
using System.Text.RegularExpressions;

namespace BatterMC.Core;

/// <summary>Process-local restricted credential; never a CLI argument, config value or UI field.</summary>
public static partial class TerminalCredentialEnvironment
{
    public const string Variable = "MUXI_TERMINAL_GAME_CREDENTIAL";
    public const string PipeVariable = "MUXI_TERMINAL_CREDENTIAL_PIPE";
    public const string BrokerVariable = "MUXI_TERMINAL_CREDENTIAL_BROKER";
    [GeneratedRegex(@"^[A-Za-z0-9_-]{43}$")]
    private static partial Regex CredentialPattern();
    public static bool Valid(string? value) => value is not null && value.Length == 43 && CredentialPattern().IsMatch(value);
    public static bool ValidAccessToken(string? value) => value is not null && Regex.IsMatch(value, @"^[A-Za-z0-9_-]{54}$");
    public static void Apply(ProcessStartInfo process, string? credential, string? pipe = null, string? broker = null)
    {
        process.Environment.Remove(Variable);
        process.Environment.Remove(PipeVariable);
        process.Environment.Remove(BrokerVariable);
        if (pipe is not null && Regex.IsMatch(pipe, @"^muxi-terminal-[0-9a-f]{32}$") && Valid(broker))
        {
            process.Environment[PipeVariable] = pipe;
            process.Environment[BrokerVariable] = broker!;
        }
        if (Valid(credential)) process.Environment[Variable] = credential!;
    }
}
