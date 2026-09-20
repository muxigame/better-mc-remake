using System.Text.Json.Serialization;

namespace BatterMC.Protocol;

/// <summary>
/// 源生成的序列化器。用它而不是反射版，裁剪后的单文件 exe 才不会在运行时
/// 因为找不到类型而炸掉（反射版编译时就会报 IL2026）。
/// </summary>
[JsonSerializable(typeof(PackManifest))]
[JsonSerializable(typeof(LauncherRelease))]
public partial class ManifestJsonContext : JsonSerializerContext;
