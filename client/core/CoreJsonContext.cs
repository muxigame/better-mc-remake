using System.Text.Json.Serialization;

namespace BatterMC.Core;

[JsonSerializable(typeof(LauncherSettings))]
[JsonSerializable(typeof(LocalState))]
[JsonSerializable(typeof(AccountSession))]
public partial class CoreJsonContext : JsonSerializerContext;
