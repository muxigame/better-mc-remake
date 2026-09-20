using System.Buffers.Binary;
using System.Text;

namespace BatterMC.Core;

public enum NbtType : byte
{
    End = 0, Byte = 1, Short = 2, Int = 3, Long = 4, Float = 5, Double = 6,
    ByteArray = 7, String = 8, List = 9, Compound = 10, IntArray = 11, LongArray = 12,
}

/// <summary>
/// 够用就好的 NBT 实现，只为了读写 servers.dat。
/// 用通用模型而不是写死结构，是为了保留玩家自己加的服务器条目和未知字段。
/// </summary>
public abstract class NbtTag
{
    public abstract NbtType Type { get; }
}

public sealed class NbtValue<T> : NbtTag
{
    public T Value { get; set; }
    private readonly NbtType _type;
    public override NbtType Type => _type;
    public NbtValue(NbtType type, T value) { _type = type; Value = value; }
}

public sealed class NbtCompound : NbtTag
{
    public override NbtType Type => NbtType.Compound;
    // 保持插入顺序，写回时不打乱原文件布局
    public List<KeyValuePair<string, NbtTag>> Entries { get; } = new();

    public NbtTag? this[string key]
    {
        get => Entries.FirstOrDefault(e => e.Key == key).Value;
        set
        {
            var idx = Entries.FindIndex(e => e.Key == key);
            if (value is null) { if (idx >= 0) Entries.RemoveAt(idx); return; }
            if (idx >= 0) Entries[idx] = new(key, value);
            else Entries.Add(new(key, value));
        }
    }

    public string? GetString(string key) => (this[key] as NbtValue<string>)?.Value;
    public void SetString(string key, string value) => this[key] = new NbtValue<string>(NbtType.String, value);
    public void SetByte(string key, byte value) => this[key] = new NbtValue<byte>(NbtType.Byte, value);
}

public sealed class NbtList : NbtTag
{
    public override NbtType Type => NbtType.List;
    public NbtType ElementType { get; set; } = NbtType.End;
    public List<NbtTag> Items { get; } = new();
}

public static class Nbt
{
    public static NbtCompound ReadUncompressed(byte[] data)
    {
        var pos = 0;
        var type = (NbtType)ReadByte(data, ref pos);
        if (type != NbtType.Compound) throw new InvalidDataException($"NBT 根节点应为 Compound，实为 {type}");
        ReadString(data, ref pos); // 根节点名，通常是空串
        return (NbtCompound)ReadPayload(data, ref pos, NbtType.Compound);
    }

    public static byte[] WriteUncompressed(NbtCompound root)
    {
        var ms = new MemoryStream();
        ms.WriteByte((byte)NbtType.Compound);
        WriteString(ms, "");
        WritePayload(ms, root);
        return ms.ToArray();
    }

    // ---------------------------------------------------------------- read

    private static NbtTag ReadPayload(byte[] d, ref int p, NbtType type)
    {
        switch (type)
        {
            case NbtType.Byte: return new NbtValue<byte>(type, ReadByte(d, ref p));
            case NbtType.Short: return new NbtValue<short>(type, ReadShort(d, ref p));
            case NbtType.Int: return new NbtValue<int>(type, ReadInt(d, ref p));
            case NbtType.Long: return new NbtValue<long>(type, ReadLong(d, ref p));
            case NbtType.Float:
                return new NbtValue<float>(type, BitConverter.Int32BitsToSingle(ReadInt(d, ref p)));
            case NbtType.Double:
                return new NbtValue<double>(type, BitConverter.Int64BitsToDouble(ReadLong(d, ref p)));
            case NbtType.String: return new NbtValue<string>(type, ReadString(d, ref p));

            case NbtType.ByteArray:
            {
                var len = ReadInt(d, ref p);
                var arr = new byte[len];
                Array.Copy(d, p, arr, 0, len); p += len;
                return new NbtValue<byte[]>(type, arr);
            }
            case NbtType.IntArray:
            {
                var len = ReadInt(d, ref p);
                var arr = new int[len];
                for (var i = 0; i < len; i++) arr[i] = ReadInt(d, ref p);
                return new NbtValue<int[]>(type, arr);
            }
            case NbtType.LongArray:
            {
                var len = ReadInt(d, ref p);
                var arr = new long[len];
                for (var i = 0; i < len; i++) arr[i] = ReadLong(d, ref p);
                return new NbtValue<long[]>(type, arr);
            }
            case NbtType.List:
            {
                var elem = (NbtType)ReadByte(d, ref p);
                var count = ReadInt(d, ref p);
                var list = new NbtList { ElementType = elem };
                for (var i = 0; i < count; i++) list.Items.Add(ReadPayload(d, ref p, elem));
                return list;
            }
            case NbtType.Compound:
            {
                var c = new NbtCompound();
                while (true)
                {
                    var t = (NbtType)ReadByte(d, ref p);
                    if (t == NbtType.End) break;
                    var name = ReadString(d, ref p);
                    c.Entries.Add(new(name, ReadPayload(d, ref p, t)));
                }
                return c;
            }
            default:
                throw new InvalidDataException($"不认识的 NBT 类型 {type}");
        }
    }

    private static byte ReadByte(byte[] d, ref int p) => d[p++];
    private static short ReadShort(byte[] d, ref int p)
    { var v = BinaryPrimitives.ReadInt16BigEndian(d.AsSpan(p)); p += 2; return v; }
    private static int ReadInt(byte[] d, ref int p)
    { var v = BinaryPrimitives.ReadInt32BigEndian(d.AsSpan(p)); p += 4; return v; }
    private static long ReadLong(byte[] d, ref int p)
    { var v = BinaryPrimitives.ReadInt64BigEndian(d.AsSpan(p)); p += 8; return v; }

    private static string ReadString(byte[] d, ref int p)
    {
        var len = (ushort)BinaryPrimitives.ReadUInt16BigEndian(d.AsSpan(p)); p += 2;
        var s = ModifiedUtf8.GetString(d, p, len);
        p += len;
        return s;
    }

    // ---------------------------------------------------------------- write

    private static void WritePayload(Stream s, NbtTag tag)
    {
        switch (tag)
        {
            case NbtValue<byte> v: s.WriteByte(v.Value); break;
            case NbtValue<short> v: WriteBE(s, BitConverter.GetBytes(v.Value)); break;
            case NbtValue<int> v: WriteBE(s, BitConverter.GetBytes(v.Value)); break;
            case NbtValue<long> v: WriteBE(s, BitConverter.GetBytes(v.Value)); break;
            case NbtValue<float> v: WriteBE(s, BitConverter.GetBytes(v.Value)); break;
            case NbtValue<double> v: WriteBE(s, BitConverter.GetBytes(v.Value)); break;
            case NbtValue<string> v: WriteString(s, v.Value); break;

            case NbtValue<byte[]> v:
                WriteBE(s, BitConverter.GetBytes(v.Value.Length));
                s.Write(v.Value); break;

            case NbtValue<int[]> v:
                WriteBE(s, BitConverter.GetBytes(v.Value.Length));
                foreach (var i in v.Value) WriteBE(s, BitConverter.GetBytes(i));
                break;

            case NbtValue<long[]> v:
                WriteBE(s, BitConverter.GetBytes(v.Value.Length));
                foreach (var i in v.Value) WriteBE(s, BitConverter.GetBytes(i));
                break;

            case NbtList list:
                s.WriteByte((byte)(list.Items.Count == 0 ? NbtType.End : list.ElementType));
                WriteBE(s, BitConverter.GetBytes(list.Items.Count));
                foreach (var item in list.Items) WritePayload(s, item);
                break;

            case NbtCompound c:
                foreach (var (name, child) in c.Entries)
                {
                    s.WriteByte((byte)child.Type);
                    WriteString(s, name);
                    WritePayload(s, child);
                }
                s.WriteByte((byte)NbtType.End);
                break;

            default:
                throw new InvalidDataException($"无法写出 {tag.GetType().Name}");
        }
    }

    /// <summary>NBT 是大端，.NET 的 BitConverter 在 x86/x64 上是小端，所以要翻转。</summary>
    private static void WriteBE(Stream s, byte[] littleEndian)
    {
        if (BitConverter.IsLittleEndian) Array.Reverse(littleEndian);
        s.Write(littleEndian);
    }

    private static void WriteString(Stream s, string value)
    {
        var bytes = ModifiedUtf8.GetBytes(value);
        if (bytes.Length > ushort.MaxValue) throw new InvalidDataException("NBT 字符串过长");
        WriteBE(s, BitConverter.GetBytes((ushort)bytes.Length));
        s.Write(bytes);
    }
}

/// <summary>
/// Java 的 modified UTF-8。和标准 UTF-8 的差别只有两处：
/// U+0000 写成 C0 80；BMP 外的字符按两个代理项各编 3 字节（CESU-8）。
/// 服务器名字里带 emoji 时，这个差别就是乱码和正常的区别。
/// </summary>
public static class ModifiedUtf8
{
    public static byte[] GetBytes(string s)
    {
        var ms = new MemoryStream(s.Length + 8);
        foreach (var c in s)
        {
            if (c is > '\0' and < '') ms.WriteByte((byte)c);
            else if (c < 'ࠀ')
            {
                ms.WriteByte((byte)(0xC0 | (c >> 6)));
                ms.WriteByte((byte)(0x80 | (c & 0x3F)));
            }
            else
            {
                ms.WriteByte((byte)(0xE0 | (c >> 12)));
                ms.WriteByte((byte)(0x80 | ((c >> 6) & 0x3F)));
                ms.WriteByte((byte)(0x80 | (c & 0x3F)));
            }
        }
        return ms.ToArray();
    }

    public static string GetString(byte[] data, int offset, int length)
    {
        var sb = new StringBuilder(length);
        var end = offset + length;
        var i = offset;
        while (i < end)
        {
            int b = data[i++];
            if ((b & 0x80) == 0) sb.Append((char)b);
            else if ((b & 0xE0) == 0xC0)
            {
                if (i >= end) break;
                sb.Append((char)(((b & 0x1F) << 6) | (data[i++] & 0x3F)));
            }
            else if ((b & 0xF0) == 0xE0)
            {
                if (i + 1 >= end) break;
                var c = ((b & 0x0F) << 12) | ((data[i++] & 0x3F) << 6) | (data[i++] & 0x3F);
                sb.Append((char)c);
            }
            else
            {
                // 标准 UTF-8 的 4 字节序列不该出现在 modified UTF-8 里，
                // 但真遇上了就按标准解码，总比丢字符好
                if (i + 2 >= end) break;
                var cp = ((b & 0x07) << 18) | ((data[i++] & 0x3F) << 12)
                       | ((data[i++] & 0x3F) << 6) | (data[i++] & 0x3F);
                sb.Append(char.ConvertFromUtf32(cp));
            }
        }
        return sb.ToString();
    }
}
