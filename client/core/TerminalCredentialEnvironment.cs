using System.Diagnostics;
using System.Text.RegularExpressions;

namespace BatterMC.Core;

/// <summary>Process-local restricted credential; never a CLI argument, config value or UI field.</summary>
public static partial class TerminalCredentialEnvironment
{
    public const string Variable = "MUXI_TERMINAL_GAME_CREDENTIAL";
    [GeneratedRegex(@"^[A-Za-z0-9_-]{43}$")]
    private static partial Regex CredentialPattern();
    public static bool Valid(string? value) => value is not null && value.Length == 43 && CredentialPattern().IsMatch(value);
    public static void Apply(ProcessStartInfo process, string? credential)
    {
        process.Environment.Remove(Variable);
        if (Valid(credential)) process.Environment[Variable] = credential!;
    }
}
