namespace BatterMC.Protocol;

/// <summary>
/// 候选列表里的协议标记。
///
/// NAT 给 TCP 和 UDP 分配的是两套互不相干的映射，两种候选必须分开带。但没必要
/// 为此给控制面加一组接口字段——它只是把字符串原样转给对端，加个前缀就够了。
/// 老版本客户端解析不了带前缀的条目，会自然跳过，不会出错。
///
/// TCP 候选还要带上**映射类型**，这一点决定了打洞该怎么打。原先两侧都只是把类型
/// 打进自己的日志，从不告诉对方，于是两边都只能假设最坏情况、都去预测对方的端口。
/// 而"两边同时猜中对方"的搜索空间是端口对 2³²，不是 2¹⁶——这就是 TCP 打洞一直
/// 一次都没成功过的根本原因，跟窗口开多宽、拨多少号都没关系。
///
/// 知道了对方的类型，分工就是确定的：
///
/// <list type="bullet">
/// <item>对方端点无关：它公布的地址就是真地址，直接照着打，不预测。</item>
/// <item>对方地址相关：它公布的端口对我无效（那是它探反射器时的映射，不是它朝我
///   发包时的映射），所以我必须预测。**两侧都要预测，但步长必须不相等**，理由见
///   <see cref="PredictStep"/>。</item>
/// </list>
/// </summary>
public static class P2PTags
{
    /// <summary>
    /// TCP 打洞默认关闭，设环境变量 MUXI_TCP_PUNCH=1 才开。客户端和 agent 读的是同一个开关。
    ///
    /// 关掉它的依据是实测：这条线路上 TCP 打洞从来没成功过——历史日志里一次都没有，
    /// 2026-09-24 蜂窝对家宽单测 TCP 一条腿又是 0/3。原因在 <see cref="TcpPuncher"/>
    /// 里算过：两侧都是地址+端口相关映射、端口顺序分配且背景消耗每秒 20 个，能不能
    /// 命中是概率事件，窗口开多宽都不保证。
    ///
    /// 而它的代价是实打实的：每次打洞一两百个并发 connect 压在两侧的 NAT 表上（服务端
    /// 那台路由器被 770 个并发打瘫过一次），客户端建立路径上多出 0.4~1.4 秒的 TCP 出口
    /// 探测，agent 每 8 秒还要连一轮反射器保鲜。UDP 打不通的线路由隧道/中转兜底。
    /// </summary>
    public static bool TcpPunchEnabled =>
        Environment.GetEnvironmentVariable("MUXI_TCP_PUNCH") == "1"
        && Environment.GetEnvironmentVariable("MUXI_NO_TCP_PUNCH") != "1";

    /// <summary>TCP 候选，映射地址相关（对称型）——对端不能直接照着这个端口打。</summary>
    public const string Tcp = "t:";

    /// <summary>TCP 候选，映射端点无关——对端照着这个地址直接连就行。</summary>
    public const string TcpEim = "te:";

    public static string TcpTagFor(bool addressDependent) => addressDependent ? Tcp : TcpEim;

    /// <summary>
    /// agent 备用打洞 socket 的 UDP 候选。
    ///
    /// 为什么要把备用的也报出去：agent 每个请求用一个 socket，取走一个、备用的顶上并重新
    /// 上报——但控制面上那份候选要等这次上报落地才会更新。两个玩家前后一秒内发起时，后一个
    /// 拿到的还是前一个正在用的那个端口，照着打满十秒必然落空（实测：同一批两个请求，一个
    /// 243ms 打通，另一个 13 秒后失败，靠后台重试才救回来）。把备用的一并报出去，新客户端
    /// 两个都打，agent 给它哪个都对得上。
    ///
    /// 老客户端解析不了带前缀的条目，会自然跳过——它们照旧只打主候选，行为不变。
    /// 也不能参与"同一地址报了多个端口 = 对称型"的判断：主备两个 socket 本来就是两个端口。
    /// </summary>
    public const string UdpAlternate = "a:";

    public static bool TryStripAlternate(string candidate, out string text)
    {
        if (candidate.StartsWith(UdpAlternate, StringComparison.Ordinal))
        {
            text = candidate[UdpAlternate.Length..];
            return true;
        }
        text = candidate;
        return false;
    }

    /// <summary>
    /// 拆一条候选字符串。
    ///
    /// <paramref name="endpointIndependent"/> 在条目带的是老的 <see cref="Tcp"/> 前缀时
    /// 为 false——老版本不带类型信息，只能按最坏情况算。这个默认值是安全方向：
    /// 宁可多预测一点，也不要照着一个不可靠的端口去打然后以为自己打过了。
    /// </summary>
    public static bool TryStripTcp(string candidate, out string text, out bool endpointIndependent)
    {
        if (candidate.StartsWith(TcpEim, StringComparison.Ordinal))
        {
            text = candidate[TcpEim.Length..];
            endpointIndependent = true;
            return true;
        }
        if (candidate.StartsWith(Tcp, StringComparison.Ordinal))
        {
            text = candidate[Tcp.Length..];
            endpointIndependent = false;
            return true;
        }
        text = candidate;
        endpointIndependent = false;
        return false;
    }

    /// <summary>
    /// 要不要预测对端端口：只看**对端**的映射类型。
    ///
    /// 对端端点无关 → 它公布的端口就是真端口，照着连，预测纯属浪费。
    /// 对端地址相关 → 它公布的端口是它探反射器时的映射，和它朝我们发包时用的不是
    /// 同一个，必须预测。这一侧自己是什么类型不影响这个判断。
    ///
    /// 注意这里**两侧都会返回 true**。看着像"两边都猜"那个老毛病，但不是——
    /// 关键在于两侧的**步长不同**，见 <see cref="PredictStep"/>。
    /// </summary>
    public static bool ShouldPredictPeerPorts(
        bool myMappingAddressDependent, bool peerEndpointIndependent, bool initiator)
        => !peerEndpointIndependent;

    /// <summary>
    /// 这一侧预测时的端口步长：发起方 1，接受方 2。**必须不相等。**
    ///
    /// 这不是调参，是方程有没有解的问题。设两侧对表时各自看到自己的外部端口
    /// <c>p_A</c>/<c>p_B</c>，开打时第 0 次 connect 实际拿到 <c>a=p_A+δ_A</c>、
    /// <c>b=p_B+δ_B</c>（δ 是对表到开打之间被整条线路消耗掉的端口数），各以步长 s
    /// 往上拨。在"地址+端口相关过滤"下命中要求：
    ///
    /// <code>
    /// 1 + s_A·i = δ_B + j        // A 的 SYN 落在 B 的真实映射上
    /// 1 + s_B·j = δ_A + i        // B 的过滤器放行 A 这一次拨号的源端口
    /// ⟹ i·(s_A·s_B − 1) = δ_A + s_B·δ_B − s_B − 1
    /// </code>
    ///
    /// **两侧步长都是 1 时分母为 0**，方程仅在 <c>δ_A+δ_B=2</c> 时有解——也就是两侧
    /// 从对表到开打之间，整条线路上一个端口都没被别的连接消耗掉。实测背景消耗
    /// 20.4 个/秒、对表提前 900ms，δ≈18。所以**窗口开到 255 个也无解**，这正是
    /// TCP 打洞长期 0/255 而两边日志都"正常"的原因。
    ///
    /// 步长取 1 和 2 时分母为 1，<c>i = δ_A + 2δ_B − 3</c>、<c>j = 1 + i − δ_B</c>，
    /// 点条件变成区间条件：δ≈18 代入得 i≈51、j≈34，窗口给到 96 就有充裕余量。
    ///
    /// 另一条同样重要：这个模型要求"第 i 次拨号拿到 a+i"，也就是**我们自己的 SYN
    /// 必须是一次紧突发**。拨号被限流摊到几秒，背景流量就会插进我们的端口序列，
    /// a+i 直接崩掉。所以 MaxInFlight 必须 ≥ 实际目标数。
    /// </summary>
    public static int PredictStep(bool initiator) => initiator ? 1 : 2;
}
