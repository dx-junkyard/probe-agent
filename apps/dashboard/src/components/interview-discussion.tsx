import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getSystemId } from "@/api/client";
import { sysKey } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { openScreenAssistant } from "@/lib/assistant-control";

type Kind = "supplement" | "correction" | "open_question" | "hypothesis";
type Target = { session_id: number; target_kind: "session" | "qa" | "intent_item" | "claim"; row_id?: number; revision_id?: number; section?: string; name?: string };
type Resolved = { ref: Target; title: string; session_title: string; reason?: string };
type Candidate = { id: number; revision: number; kind: Kind; text: string; decision: string; sources: { role: string; quote: string; turn_id?: number }[]; targets: { target: Target; title: string; session_title?: string; selection: string; freshness: string; reason: string }[] };
type Job = { id?: number; state: string; error_code?: string; targets?: Resolved[]; next_cursor?: number | null; remaining?: boolean };
type Saved = { id: number; contribution_id: number; text: string; kind: Kind; origin_url: string; application: unknown; target: { target: Target; freshness: string }; sources: Candidate["sources"] };
type Preview = { preview_token: string; items: { link_id: number; before: string; after: string; effect: string; title: string }[] };
const labels: Record<Kind, string> = { supplement: "補足", correction: "訂正", open_question: "未解決の問い", hypothesis: "仮説" };
const id = () => crypto.randomUUID();
const errorMessage = (e: unknown) => {
  const code = e instanceof ApiError ? e.code : undefined;
  const messages: Record<string, string> = {
    target_stale: "参照した内容が変わりました。関連先を選び直して確認してください。",
    candidate_revision_mismatch: "別の編集が保存されています。最新の候補を読み直してください。",
    session_closed: "このインタビューは終了しています。再開してから保存してください。",
    reasoning_unavailable: "AIによる整理を利用できません。設定を確認するか、手入力で補足してください。",
    preview_expired: "確認画面の有効期限が切れました。もう一度差分を確認してください。",
    application_conflict: "反映内容が競合しています。項目ごとに確認してください。",
  };
  return (code && messages[code]) || "操作を完了できませんでした。内容は保持されています。同じ操作を再試行できます。";
};

function useInterviewDiscussionEnabled() {
  return useQuery({ queryKey: sysKey("interview-discussion-capabilities"), queryFn: () => api.get<{ enabled: boolean }>("/assistant/interview-discussion-capabilities"), retry: false, staleTime: 60000 }).data?.enabled === true;
}

function CandidateEditor({ item, onChange }: { item: Candidate; onChange: () => void }) {
  const [text, setText] = useState(item.text);
  const [kind, setKind] = useState<Kind>(item.kind);
  const [selected, setSelected] = useState<Target[]>(item.targets.filter(t => t.selection === "selected").map(t => t.target));
  const [sid, setSid] = useState(String(item.targets[0]?.target.session_id ?? ""));
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState<number>();
  const requests = useRef(new Map<string, string>());
  const keyFor = (payload: unknown) => {
    const key = JSON.stringify(payload);
    if (!requests.current.has(key)) requests.current.set(key, id());
    return requests.current.get(key)!;
  };
  const targets = useQuery({ queryKey: [...sysKey("interview-discussion-targets"), sid], queryFn: () => api.get<{ targets: Resolved[] }>(`/interview/sessions/${sid}/discussion-targets`), enabled: /^\d+$/.test(sid) && Number(sid) > 0, retry: false });
  const searchJob = useQuery({ queryKey: [...sysKey("interview-discussion-job"), jobId], queryFn: () => api.get<Job>(`/assistant/interview-extractions/${jobId}`), enabled: !!jobId, refetchInterval: q => ["queued", "running"].includes(q.state.data?.state ?? "") ? 1500 : false });
  const choices: Resolved[] = [...item.targets.map(t => ({ ref: t.target, title: t.title, session_title: t.session_title ?? `Interview #${t.target.session_id}`, reason: t.reason })), ...(targets.data?.targets ?? []), ...(searchJob.data?.targets ?? [])];
  const unique = [...new Map(choices.map(t => [JSON.stringify(t.ref), t])).values()];
  function toggle(t: Target) {
    const key = JSON.stringify(t);
    setSelected(prev => prev.some(p => JSON.stringify(p) === key) ? prev.filter(p => JSON.stringify(p) !== key) : [...prev, t]);
  }
  async function edit(decision: string) {
    const payload = { expected_revision: item.revision, text, kind, decision, selected_targets: selected };
    setBusy(true); setNotice("");
    try { await api.post(`/assistant/interview-candidates/${item.id}/revisions`, { ...payload, request_id: keyFor(payload) }); onChange(); }
    catch (e) { setNotice(errorMessage(e)); }
    finally { setBusy(false); }
  }
  async function search(cursor = 0) {
    setBusy(true);
    try {
      const job = await api.post<Job>(`/assistant/interview-candidates/${item.id}/target-search`, { request_id: id(), expected_revision: item.revision, cursor });
      setJobId(job.id);
    } catch (e) { setNotice(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <details className="rounded border p-2" data-testid={`interview-candidate-${item.id}`}>
    <summary className="cursor-pointer text-sm">{labels[item.kind]} · {item.text.slice(0, 60)} · {item.decision === "saved" ? "保存済み" : item.decision === "held" ? "保留" : item.decision === "rejected" ? "却下" : "未確認"}</summary>
    <div className="space-y-2 pt-2">
      <label className="block text-xs">分類<select className="ml-2 border rounded" value={kind} onChange={e => setKind(e.target.value as Kind)}>{Object.entries(labels).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label className="block text-xs">整理文（既存内容へ反映する場合は更新後の全文）<textarea className="w-full rounded border p-2" rows={4} maxLength={4000} value={text} onChange={e => setText(e.target.value)} /></label>
      <details><summary className="text-xs">根拠の発言</summary>{item.sources.map((s,i) => <blockquote key={i} className="border-l pl-2 text-xs whitespace-pre-wrap">{s.role === "user" ? "ユーザー" : s.role === "manual" ? "レビューで追記" : "AI（推測を含む）"}{s.turn_id ? ` · 発言 #${s.turn_id}` : ""}: {s.quote}</blockquote>)}</details>
      <label className="block text-xs">関連先のInterview番号<input className="ml-2 w-20 border" type="number" min="1" value={sid} onChange={e => setSid(e.target.value)} /></label>
      {targets.isError && <p role="alert" className="text-xs">関連先を取得できません。番号を確認してください。</p>}
      <fieldset className="max-h-44 overflow-auto space-y-1"><legend className="text-xs">保存する関連先を選択</legend>{unique.map(t => <label key={JSON.stringify(t.ref)} className="flex gap-2 text-xs"><input type="checkbox" checked={selected.some(s => JSON.stringify(s) === JSON.stringify(t.ref))} onChange={() => toggle(t.ref)} /><span>{t.session_title} · {t.title}{t.reason && <small className="block">{t.reason}</small>}</span></label>)}</fieldset>
      <Button size="sm" variant="outline" disabled={busy} onClick={() => search()}>関連するインタビューを探す</Button>
      {searchJob.data && <p className="text-xs" role="status">{searchJob.data.state === "succeeded" ? "関連候補を取得しました。保存先は選択して確認してください。" : searchJob.data.state === "failed" ? "関連探索に失敗しました。再試行できます。" : "関連を探しています…"}</p>}
      {searchJob.data?.next_cursor && <Button size="sm" variant="outline" onClick={() => search(searchJob.data!.next_cursor!)}>関連をさらに探す</Button>}
      <div className="flex flex-wrap gap-2"><Button size="sm" disabled={busy || !text.trim()} onClick={() => edit("pending")}>{item.decision === "saved" ? "訂正版の候補を作る" : "編集・関連先を確認"}</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => edit("held")}>保留</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => edit("rejected")}>却下</Button></div>
      <p className="text-xs" role="status">{notice}</p>
    </div>
  </details>;
}

export function InterviewDiscussionReview({ threadId, sessionId, throughTurn }: { threadId: number; sessionId: number; throughTurn: number }) {
  const enabled = useInterviewDiscussionEnabled();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number[]>([]);
  const [manual, setManual] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState<number>();
  const [cursor, setCursor] = useState(0);
  const pending = useRef<{ signature: string; id: string } | null>(null);
  const queryKey = [...sysKey("interview-discussion-candidates"), threadId, cursor];
  const query = useQuery({ queryKey, queryFn: () => api.get<{ items: Candidate[]; next_cursor: number | null; latest_job?: Job }>(`/assistant/discussion-threads/${threadId}/interview-candidates?cursor=${cursor}`), enabled, refetchInterval: enabled ? 3000 : false });
  const job = useQuery({ queryKey: [...sysKey("interview-discussion-job"), jobId], queryFn: () => api.get<Job>(`/assistant/interview-extractions/${jobId}`), enabled: enabled && !!jobId, refetchInterval: q => ["queued", "running"].includes(q.state.data?.state ?? "") ? 1000 : false });
  async function run(path: string, payload: object, success: string) {
    const signature = JSON.stringify([path, payload]);
    if (pending.current?.signature !== signature) pending.current = { signature, id: id() };
    const requestId = pending.current.id;
    setBusy(true); setNotice("");
    try {
      const result = await api.post<Job>(path, { ...payload, request_id: requestId });
      if (result.id && "state" in result) setJobId(result.id);
      pending.current = null;
      setNotice(success); setSelected([]);
      await qc.invalidateQueries({ queryKey: sysKey("interview-discussion-candidates") });
      await qc.invalidateQueries({ queryKey: sysKey("interview-discussion-saved") });
    } catch (e) { setNotice(errorMessage(e)); }
    finally { setBusy(false); }
  }
  if (!enabled) return null;
  const candidates = query.data?.items ?? [];
  const activeJob = job.data ?? query.data?.latest_job;
  return <section className="space-y-2 border-t pt-3" aria-label="インタビューの整理候補">
    <div className="flex items-center justify-between"><p className="text-sm font-medium">整理候補 {candidates.filter(c => c.decision === "pending").length}件</p><Button size="sm" variant="outline" disabled={busy || throughTurn < 1} onClick={() => run(`/assistant/discussion-threads/${threadId}/interview-extractions`, { through_turn_number: throughTurn }, "整理を開始しました。会話は続けられます。")}>ここまでを整理</Button></div>
    {activeJob && <p role="status" className="text-xs">{activeJob.state === "failed" ? "AIによる整理に失敗しました。「ここまでを整理」で再試行、または手入力で補足できます。" : ["queued", "running"].includes(activeJob.state) ? "発言を整理しています…" : activeJob.remaining ? "まだ未整理の発言があります。もう一度「ここまでを整理」で続きを処理できます。" : candidates.length ? "整理が完了しました。" : "今回は内容の更新はありません。"}</p>}
    {query.isError && <Button size="sm" onClick={() => query.refetch()}>候補を再取得する</Button>}
    {candidates.map(c => <div key={c.id} className="space-y-1"><CandidateEditor key={`${c.id}:${c.revision}`} item={c} onChange={() => { setSelected([]); void query.refetch(); }} />{["pending","held"].includes(c.decision) && <label className="text-xs flex gap-2"><input type="checkbox" checked={selected.includes(c.id)} disabled={!c.targets.some(t => t.selection === "selected") || c.targets.some(t => t.selection === "selected" && t.freshness !== "current")} onChange={() => setSelected(prev => prev.includes(c.id) ? prev.filter(i => i !== c.id) : [...prev,c.id])} />この補足を保存する</label>}</div>)}
    {cursor > 0 && <Button variant="outline" size="sm" onClick={() => { setCursor(0); setSelected([]); }}>最初の候補へ</Button>}{query.data?.next_cursor && <Button variant="outline" size="sm" onClick={() => { setCursor(query.data!.next_cursor!); setSelected([]); }}>次の候補</Button>}
    <Button size="sm" disabled={busy || !selected.length} onClick={() => run(`/assistant/discussion-threads/${threadId}/interview-contributions`, { items: candidates.filter(c => selected.includes(c.id)).map(c => ({ candidate_id:c.id,expected_revision:c.revision })) }, "選択した補足を保存しました。既存内容への反映と正準理解への昇格は別操作です。")}>選択した補足を保存</Button>
    <details><summary className="text-xs cursor-pointer">手入力で補足する</summary><textarea aria-label="手入力の補足" className="w-full border rounded p-2" value={manual} maxLength={4000} onChange={e => setManual(e.target.value)} /><Button size="sm" disabled={busy || !manual.trim()} onClick={() => run(`/interview/sessions/${sessionId}/discussion-contribution-drafts`, { kind:"supplement",text:manual }, "手入力の候補を作りました。関連先を確認して保存してください。")}>候補を作る</Button></details>
    <p className="text-xs" role="status">{notice}</p>
  </section>;
}

export function InterviewContributions({ sessionId }: { sessionId: number }) {
  const enabled = useInterviewDiscussionEnabled();
  const qc = useQueryClient();
  const [preview, setPreview] = useState<Preview>();
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [cursor, setCursor] = useState(0);
  const applyId = useRef(id());
  const query = useQuery({ queryKey: [...sysKey("interview-discussion-saved"),sessionId,cursor], queryFn: () => api.get<{ items: Saved[]; next_cursor: number | null }>(`/interview/sessions/${sessionId}/discussion-contributions?cursor=${cursor}`), enabled });
  async function inspect(item: Saved) {
    setBusy(true); setNotice("");
    try { setPreview(await api.post<Preview>(`/interview/sessions/${sessionId}/discussion-applications/preview`, { request_id:id(),contribution_link_ids:[item.id] })); applyId.current=id(); }
    catch(e) { setNotice(errorMessage(e)); }
    finally { setBusy(false); }
  }
  async function apply() {
    if (!preview) return;
    const system = getSystemId();
    setBusy(true);
    try {
      await api.post(`/interview/sessions/${sessionId}/discussion-applications`, { request_id:applyId.current,preview_token:preview.preview_token,selected_link_ids:preview.items.map(i=>i.link_id) });
      if (getSystemId() !== system) return;
      setPreview(undefined); setNotice("既存内容へ反映しました。System正準理解への昇格は行っていません。");
      await qc.invalidateQueries();
    } catch(e) { setNotice(errorMessage(e)); }
    finally { setBusy(false); }
  }
  if (!enabled) return null;
  return <section className="rounded border p-3 space-y-3" aria-label="会話からの補足">
    <div className="flex flex-wrap justify-between gap-2"><h2 className="font-medium">会話からの補足</h2><Button variant="outline" size="sm" onClick={() => openScreenAssistant(undefined,sessionId)}>この内容について話す</Button></div>
    <p className="text-xs text-muted-foreground">自由に質問して理解を深め、わかったことを補足できます。保存した補足はユーザーの申告として次回の理解更新で参照します。</p>
    {query.isError && <Button onClick={() => query.refetch()}>補足を再取得</Button>}
    {query.data?.items.map(item => <article key={item.id} className="rounded border p-2 space-y-2"><p className="text-xs">{labels[item.kind]} · {item.application ? "反映済み" : "補足として保存済み"}</p><p className="text-sm whitespace-pre-wrap">{item.text}</p><a className="text-xs underline" href={item.origin_url}>起点の会話を開く</a><details><summary className="text-xs">根拠の発言</summary>{item.sources.map((s,i)=><blockquote key={i} className="text-xs whitespace-pre-wrap">{s.quote}</blockquote>)}</details>{!item.application && item.target.target.target_kind !== "session" && ["supplement","correction"].includes(item.kind) && <Button size="sm" variant="outline" disabled={busy || item.target.freshness !== "current"} onClick={() => inspect(item)}>既存の内容へ反映</Button>}{item.target.freshness !== "current" && <p className="text-xs">参照先が変わっています。起点の会話で訂正版の候補を作ってください。</p>}</article>)}
    {cursor > 0 && <Button size="sm" onClick={() => setCursor(0)}>最初へ</Button>}{query.data?.next_cursor && <Button size="sm" onClick={() => setCursor(query.data!.next_cursor!)}>次の補足</Button>}
    {preview && <div className="border rounded p-3 space-y-2" role="region" aria-label="反映前の確認">{preview.items.map(i=><div key={i.link_id}><p className="font-medium">{i.title}</p><p className="text-xs whitespace-pre-wrap">現在: {i.before}</p><p className="text-xs whitespace-pre-wrap">更新後: {i.after}</p></div>)}<Button disabled={busy} onClick={apply}>{preview.items[0]?.effect === "confirm_intent" ? "意図の訂正を確認・保存する" : preview.items[0]?.effect === "answer_question" ? "回答として保存する" : "要約の候補版を保存する"}</Button><Button variant="outline" disabled={busy} onClick={()=>setPreview(undefined)}>戻る</Button></div>}
    <p role="status" className="text-xs">{notice}</p>
  </section>;
}
