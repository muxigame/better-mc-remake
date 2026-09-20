using System.Text.Json.Serialization;

namespace BatterMC.Core;

[JsonSerializable(typeof(LauncherSettings))]
[JsonSerializable(typeof(LocalState))]
public partial class CoreJsonContext : JsonSerializerContext;
