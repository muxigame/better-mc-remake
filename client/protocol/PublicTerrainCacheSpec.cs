namespace BatterMC.Protocol;

/// <summary>Approved public terrain, installed before Minecraft starts. Cache files are never Managed.</summary>
public sealed class PublicTerrainCacheSpec
{
    public string PackagePath { get; set; } = "";
    public string PackageSha256 { get; set; } = "";
    public string GeneratorPath { get; set; } = "";
    public string DhPath { get; set; } = "";
    public string GeneratorSha256 { get; set; } = "";
    public string DemSha256 { get; set; } = "";
    public string DhSha256 { get; set; } = "";
    public string HashedSeed { get; set; } = "";
    public string ServerFolderKey { get; set; } = "";
    public string Dimension { get; set; } = "muxi_minigames:flight_iwo_half";
    public int MetresPerBlock { get; set; } = 2;
    public int RoomSpacing { get; set; } = 49152;
    public int RoomSlot { get; set; }
}
