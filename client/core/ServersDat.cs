using BatterMC.Protocol;

namespace BatterMC.Core;

/// <summary>
/// 维护多人游戏服务器列表。
/// servers.dat 是未压缩的 NBT（和 level.dat 不一样，那个是 gzip 的）。
///
/// Forced 的条目每次启动都会写回列表顶部，玩家在游戏里删掉也会回来；
/// 玩家自己加的其他服务器原样保留。
/// </summary>
public static class ServersDat
{
    public static void EnsureServers(LauncherPaths paths, IEnumerable<ServerEntry> servers)
    {
        var forced = servers.Where(s => s.Forced && !string.IsNullOrWhiteSpace(s.Host)).ToList();
        if (forced.Count == 0) return;

        var file = Path.Combine(paths.GameDir, "servers.dat");
        NbtCompound root;
        NbtList list;

        try
        {
            if (File.Exists(file))
            {
                root = Nbt.ReadUncompressed(File.ReadAllBytes(file));
                list = root["servers"] as NbtList ?? new NbtList { ElementType = NbtType.Compound };
            }
            else
            {
                root = new NbtCompound();
                list = new NbtList { ElementType = NbtType.Compound };
            }
        }
        catch (Exception ex)
        {
            Log.Warn($"servers.dat 读取失败，重建：{ex.Message}");
            try
            {
                if (File.Exists(file)) File.Copy(file, file + ".broken", overwrite: true);
            }
            catch { }
            root = new NbtCompound();
            list = new NbtList { ElementType = NbtType.Compound };
        }

        var changed = false;

        // 倒序插入，保证清单里的顺序就是列表里从上到下的顺序
        foreach (var server in Enumerable.Reverse(forced))
        {
            var address = server.Port == 25565 ? server.Host : $"{server.Host}:{server.Port}";

            var existing = list.Items.OfType<NbtCompound>()
                .FirstOrDefault(c => string.Equals(c.GetString("ip"), address, StringComparison.OrdinalIgnoreCase))
                ?? list.Items.OfType<NbtCompound>()
                    .FirstOrDefault(c => string.Equals(c.GetString("name"), server.Name, StringComparison.OrdinalIgnoreCase));

            if (existing is not null)
            {
                if (existing.GetString("name") != server.Name)
                {
                    existing.SetString("name", server.Name);
                    changed = true;
                }
                if (existing.GetString("ip") != address)
                {
                    existing.SetString("ip", address);
                    changed = true;
                }
                existing.SetByte("acceptTextures", 1);
                // 已存在就挪到最前面
                if (list.Items.IndexOf(existing) != 0)
                {
                    list.Items.Remove(existing);
                    list.Items.Insert(0, existing);
                    changed = true;
                }
                continue;
            }

            var entry = new NbtCompound();
            entry.SetString("name", server.Name);
            entry.SetString("ip", address);
            entry.SetByte("acceptTextures", 1); // 自动接受服务器资源包，省一次弹窗
            list.Items.Insert(0, entry);
            changed = true;
            Log.Info($"写入服务器条目：{server.Name} ({address})");
        }

        if (!changed && root["servers"] is not null) return;

        list.ElementType = NbtType.Compound;
        root["servers"] = list;
        AtomicFile.WriteAllBytes(file, Nbt.WriteUncompressed(root));
    }
}
