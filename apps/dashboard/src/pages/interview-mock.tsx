import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Clock3,
  FileText,
  Flag,
  HelpCircle,
  History,
  Lightbulb,
  Link2,
  LayoutGrid,
  MessageSquareText,
  MoreHorizontal,
  Network,
  Pencil,
  Settings,
  ShieldCheck,
  Sparkles,
  Target,
  Users,
} from "lucide-react";

const understanding = [
  {
    key: "vision",
    number: "01",
    title: "Vision",
    caption: "誰の状態を、どう変えたいか",
    summary: "未設定",
    status: "missing",
    hint: "目指す状態・価値・成功の定義を回答",
    icon: Flag,
  },
  {
    key: "purpose",
    number: "02",
    title: "System purpose",
    caption: "このシステムが担う役割",
    summary: "実行データを収集し、候補実装と比較する",
    status: "confirmed",
    hint: "内容を確認済み。必要なら直接編集",
    icon: Target,
  },
  {
    key: "capabilities",
    number: "03",
    title: "Capabilities",
    caption: "目的を支える主要機能",
    summary: "5項目中 4項目を確認済み",
    status: "review",
    hint: "「実行トレース管理」の責務を確認",
    icon: Sparkles,
  },
  {
    key: "boundaries",
    number: "04",
    title: "Boundaries",
    caption: "外部との接点・責務境界",
    summary: "API 8件・外部連携 2件",
    status: "confirmed",
    hint: "内容を確認済み。必要なら直接編集",
    icon: Link2,
  },
  {
    key: "constraints",
    number: "05",
    title: "Constraints",
    caption: "守るべき制約・非機能要件",
    summary: "3件中 1件に根拠不足",
    status: "review",
    hint: "性能要件の根拠を追加",
    icon: ShieldCheck,
  },
];

const qa = [
  { label: "回答済み", value: 8, tone: "bg-emerald-500" },
  { label: "確認待ち", value: 2, tone: "bg-amber-400" },
  { label: "未回答", value: 1, tone: "bg-slate-300" },
];

function StatusPill({ status }: { status: string }) {
  if (status === "confirmed") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700 ring-1 ring-inset ring-emerald-200">
        <CheckCircle2 className="h-3 w-3" /> 確認済み
      </span>
    );
  }
  if (status === "missing") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2.5 py-1 text-[11px] font-semibold text-rose-700 ring-1 ring-inset ring-rose-200">
        <CircleDashed className="h-3 w-3" /> 未設定
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-700 ring-1 ring-inset ring-amber-200">
      <AlertTriangle className="h-3 w-3" /> 要確認
    </span>
  );
}

export default function InterviewMockPage() {
  return (
    <div className="h-screen overflow-hidden bg-white text-slate-900">
      <header className="flex h-14 items-center border-b border-slate-200 bg-white px-4">
        <div className="flex w-[205px] items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-slate-950 text-sm font-bold text-white">P</span>
          <span className="text-sm font-bold">Probe Agent</span>
        </div>
        <button className="ml-2 inline-flex h-9 min-w-[225px] items-center justify-between rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold shadow-sm">
          probe-agent / self-test <ChevronRight className="h-3.5 w-3.5 rotate-90 text-slate-400" />
        </button>
        <div className="ml-auto hidden items-center gap-4 text-[11px] text-slate-400 lg:flex">
          <span className="font-semibold text-slate-700">分析準備</span>
          <span>プローブ設定</span><span>観測</span><span>候補評価</span><span>公開</span>
          <span className="h-5 w-px bg-slate-200" />
          <span className="font-medium text-slate-600">urakawa</span>
        </div>
      </header>
      <div className="flex h-[calc(100vh-56px)]">
        <aside className="hidden w-[220px] shrink-0 border-r border-slate-200 bg-white px-3 py-4 md:block">
          <nav className="space-y-1 text-xs">
            <div className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-slate-500"><LayoutGrid className="h-4 w-4" />Overview</div>
            <div className="px-3 pb-1 pt-4 text-[9px] font-bold uppercase tracking-[0.14em] text-slate-400">Setup</div>
            <div className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-slate-500"><Settings className="h-4 w-4" />Settings</div>
            <div className="px-3 pb-1 pt-4 text-[9px] font-bold uppercase tracking-[0.14em] text-slate-400">Understand</div>
            <div className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-slate-500"><Sparkles className="h-4 w-4" />System understanding</div>
            <div className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-slate-500"><Network className="h-4 w-4" />Capability map</div>
            <div className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-slate-500"><Link2 className="h-4 w-4" />Flow explorer</div>
            <div className="flex items-center gap-2.5 rounded-lg bg-slate-100 px-3 py-2 font-semibold text-slate-950"><MessageSquareText className="h-4 w-4" />Interview</div>
            <div className="px-3 pb-1 pt-4 text-[9px] font-bold uppercase tracking-[0.14em] text-slate-400">Instrument</div>
            <div className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-slate-400"><Target className="h-4 w-4" />Probe planner</div>
          </nav>
        </aside>
        <main className="min-w-0 flex-1 overflow-y-auto bg-[#f7f8fa]">
    <div className="min-h-full bg-[#f7f8fa] text-slate-900">
      <div className="mx-auto max-w-[1680px] px-5 py-5 xl:px-8">
        <header className="mb-5 flex flex-col justify-between gap-4 xl:flex-row xl:items-start">
          <div>
            <div className="mb-1 flex items-center gap-2 text-xs font-medium text-slate-500">
              <span>System understanding</span>
              <ChevronRight className="h-3.5 w-3.5" />
              <span>Interview #13</span>
            </div>
            <div className="flex items-center gap-3">
              <h1 className="text-[26px] font-bold tracking-tight">インタビュー・コックピット</h1>
              <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-500 ring-1 ring-slate-200">Snapshot 31</span>
            </div>
            <p className="mt-1 text-sm text-slate-500">理解の全体像を確認し、曖昧な箇所を順番に解消します。</p>
          </div>
          <div className="flex items-center gap-2">
            <button className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 shadow-sm">
              <History className="h-4 w-4" /> 変更履歴
            </button>
            <button className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 shadow-sm">
              #13 · open <ChevronRight className="h-3.5 w-3.5 rotate-90" />
            </button>
          </div>
        </header>

        <section className="mb-5 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
          <div className="grid lg:grid-cols-[minmax(0,1fr)_370px]">
            <div className="p-5 xl:p-6">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="grid h-11 w-11 place-items-center rounded-xl bg-slate-950 text-white">
                    <MessageSquareText className="h-5 w-5" />
                  </div>
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">Interview status</p>
                    <h2 className="mt-0.5 text-lg font-bold">全体像はほぼ完成。あと3件の確認が必要です</h2>
                  </div>
                </div>
                <div className="flex items-baseline gap-1">
                  <span className="text-3xl font-bold tracking-tight">72</span>
                  <span className="text-sm font-semibold text-slate-400">%</span>
                </div>
              </div>
              <div className="mt-5 h-2 overflow-hidden rounded-full bg-slate-100">
                <div className="h-full w-[72%] rounded-full bg-gradient-to-r from-emerald-500 to-teal-400" />
              </div>
              <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
                {[
                  ["5", "理解カテゴリ"],
                  ["2", "要確認"],
                  ["1", "未設定"],
                  ["11", "質問合計"],
                ].map(([value, label]) => (
                  <div key={label} className="rounded-xl bg-slate-50 px-3.5 py-3">
                    <span className="text-lg font-bold">{value}</span>
                    <span className="ml-2 text-xs text-slate-500">{label}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="border-t border-slate-200 bg-amber-50/70 p-5 lg:border-l lg:border-t-0 xl:p-6">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.13em] text-amber-700">
                <Lightbulb className="h-4 w-4" /> 次にやること
              </div>
              <h3 className="mt-2 text-base font-bold">Visionを1問で定義する</h3>
              <p className="mt-1 text-xs leading-5 text-slate-600">「誰を、どんな状態にしたいか」を回答すると、全体の目的と成功基準がつながります。</p>
              <button className="mt-4 inline-flex h-9 w-full items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-xs font-semibold text-white shadow-sm">
                質問に回答する <ArrowRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </section>

        <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_390px]">
          <main className="space-y-5">
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)] xl:p-6">
              <div className="mb-5 flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-base font-bold">理解の全体マップ</h2>
                  <p className="mt-1 text-xs text-slate-500">カードを選択すると、根拠と修正方法を右側に表示します。</p>
                </div>
                <div className="hidden items-center gap-3 text-[11px] text-slate-500 sm:flex">
                  <span className="inline-flex items-center gap-1"><i className="h-2 w-2 rounded-full bg-emerald-500" />確認済み</span>
                  <span className="inline-flex items-center gap-1"><i className="h-2 w-2 rounded-full bg-amber-400" />要確認</span>
                  <span className="inline-flex items-center gap-1"><i className="h-2 w-2 rounded-full bg-rose-400" />未設定</span>
                </div>
              </div>

              <div className="relative">
                <div className="absolute left-[9%] right-[9%] top-7 hidden h-px bg-slate-200 lg:block" />
                <div className="relative grid gap-3 md:grid-cols-2 lg:grid-cols-5">
                  {understanding.map((item) => {
                    const Icon = item.icon;
                    const selected = item.key === "vision";
                    return (
                      <button
                        key={item.key}
                        className={`group relative min-h-[190px] rounded-xl border p-4 text-left transition ${
                          selected
                            ? "border-slate-900 bg-slate-950 text-white shadow-lg shadow-slate-200"
                            : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm"
                        }`}
                      >
                        <div className={`mb-4 flex h-12 w-12 items-center justify-center rounded-xl ring-4 ${selected ? "bg-white text-slate-950 ring-slate-800" : "bg-slate-50 text-slate-700 ring-white"}`}>
                          <Icon className="h-5 w-5" />
                        </div>
                        <div className={`text-[10px] font-bold tracking-[0.16em] ${selected ? "text-slate-400" : "text-slate-400"}`}>{item.number}</div>
                        <h3 className="mt-1 text-sm font-bold">{item.title}</h3>
                        <p className={`mt-1 min-h-8 text-[11px] leading-4 ${selected ? "text-slate-400" : "text-slate-500"}`}>{item.caption}</p>
                        <div className="mt-3"><StatusPill status={item.status} /></div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="mt-4 grid gap-2 lg:grid-cols-5">
                {understanding.map((item) => (
                  <div key={item.key} className="rounded-lg bg-slate-50 px-3 py-2.5 text-[11px] leading-4 text-slate-600">
                    <div className="mb-1 font-semibold text-slate-800">{item.summary}</div>
                    {item.hint}
                  </div>
                ))}
              </div>
            </section>

            <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-sm font-bold">未解決の確認事項</h2>
                    <p className="mt-1 text-xs text-slate-500">影響が大きい順に並んでいます。</p>
                  </div>
                  <span className="rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-700">残り 3</span>
                </div>
                <div className="mt-4 divide-y divide-slate-100">
                  {[
                    ["高", "Vision", "このシステムによって、誰のどんな状態を実現したいですか？", "回答する"],
                    ["中", "Capability", "実行トレース管理は独立した責務ですか？", "確認する"],
                    ["中", "Constraint", "応答時間 3秒以内という要件の根拠は？", "根拠を追加"],
                  ].map(([priority, category, question, action], index) => (
                    <div key={question} className="flex gap-3 py-3.5 first:pt-0 last:pb-0">
                      <span className={`mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg text-[10px] font-bold ${index === 0 ? "bg-rose-50 text-rose-700" : "bg-amber-50 text-amber-700"}`}>{priority}</span>
                      <div className="min-w-0 flex-1">
                        <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">{category}</div>
                        <p className="mt-0.5 text-xs font-semibold leading-5 text-slate-800">{question}</p>
                      </div>
                      <button className="self-center whitespace-nowrap text-[11px] font-bold text-slate-700 underline decoration-slate-300 underline-offset-4">{action}</button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-bold">Q&Aの進捗</h2>
                  <HelpCircle className="h-4 w-4 text-slate-400" />
                </div>
                <div className="mt-5 flex items-center justify-center">
                  <div className="relative grid h-28 w-28 place-items-center rounded-full" style={{ background: "conic-gradient(#10b981 0 72%, #fbbf24 72% 90%, #e2e8f0 90% 100%)" }}>
                    <div className="grid h-[82px] w-[82px] place-items-center rounded-full bg-white text-center">
                      <div><span className="text-2xl font-bold">8</span><span className="text-xs text-slate-400">/11</span><div className="text-[10px] text-slate-500">回答済み</div></div>
                    </div>
                  </div>
                </div>
                <div className="mt-5 space-y-2">
                  {qa.map((item) => (
                    <div key={item.label} className="flex items-center text-xs">
                      <i className={`mr-2 h-2 w-2 rounded-full ${item.tone}`} />
                      <span className="text-slate-500">{item.label}</span>
                      <span className="ml-auto font-bold">{item.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </section>
          </main>

          <aside className="space-y-4 xl:sticky xl:top-5">
            <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.08)]">
              <div className="border-b border-slate-100 bg-slate-50/80 px-5 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">Selected · 01</div>
                    <h2 className="mt-1 text-lg font-bold">Vision</h2>
                    <p className="mt-0.5 text-xs text-slate-500">誰の状態を、どのように変えたいか</p>
                  </div>
                  <button className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 hover:bg-white"><MoreHorizontal className="h-4 w-4" /></button>
                </div>
              </div>
              <div className="p-5">
                <div className="rounded-xl border border-rose-200 bg-rose-50 p-4">
                  <div className="flex items-center gap-2 text-xs font-bold text-rose-700">
                    <AlertTriangle className="h-4 w-4" /> この項目は未設定です
                  </div>
                  <p className="mt-2 text-[11px] leading-5 text-slate-600">コードやドキュメントだけでは判断できません。事業・利用者の視点から回答してください。</p>
                </div>

                <div className="mt-5">
                  <h3 className="text-xs font-bold">修正するには</h3>
                  <div className="mt-3 space-y-2">
                    {[
                      [MessageSquareText, "質問に回答", "AIとの1問1答で定義する", "推奨"],
                      [Pencil, "直接編集", "文章を自分で入力する", ""],
                      [FileText, "根拠を追加", "ドキュメントやコードを紐づける", ""],
                    ].map(([ActionIcon, title, description, badge]) => {
                      const Icon = ActionIcon as typeof MessageSquareText;
                      return (
                        <button key={title as string} className="flex w-full items-center gap-3 rounded-xl border border-slate-200 p-3 text-left hover:border-slate-400 hover:bg-slate-50">
                          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-700"><Icon className="h-4 w-4" /></span>
                          <span className="min-w-0 flex-1"><span className="flex items-center gap-2 text-xs font-bold">{title as string}{badge && <i className="rounded bg-emerald-50 px-1.5 py-0.5 text-[9px] not-italic text-emerald-700">{badge as string}</i>}</span><span className="mt-0.5 block text-[10px] text-slate-500">{description as string}</span></span>
                          <ChevronRight className="h-4 w-4 text-slate-300" />
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="mt-5 border-t border-slate-100 pt-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-xs font-bold">この項目への影響</h3>
                    <span className="text-[10px] text-slate-400">下流 3項目</span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {["System purpose", "成功基準", "Probe flow"].map(label => <span key={label} className="rounded-md bg-slate-100 px-2 py-1 text-[10px] font-medium text-slate-600">{label}</span>)}
                  </div>
                </div>
              </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
              <div className="flex items-center justify-between">
                <h2 className="text-xs font-bold">セッション情報</h2>
                <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-700"><i className="h-1.5 w-1.5 rounded-full bg-emerald-500" />保存済み</span>
              </div>
              <dl className="mt-3 grid grid-cols-[100px_1fr] gap-y-2 text-[11px]">
                <dt className="text-slate-400">参加者</dt><dd className="flex items-center gap-1.5 font-medium"><Users className="h-3.5 w-3.5 text-slate-400" />urakawa</dd>
                <dt className="text-slate-400">最終更新</dt><dd className="flex items-center gap-1.5 font-medium"><Clock3 className="h-3.5 w-3.5 text-slate-400" />2分前</dd>
                <dt className="text-slate-400">根拠</dt><dd className="font-medium">コード 14 · 文書 6</dd>
              </dl>
            </section>
          </aside>
        </div>
      </div>
    </div>
        </main>
      </div>
    </div>
  );
}
