using System.Text.Json.Serialization;
namespace BatterMC.Core;
// Harness context: serialize the actual production LocalState without unrelated account types.
[JsonSerializable(typeof(LocalState))]
public partial class CoreJsonContext : JsonSerializerContext;
